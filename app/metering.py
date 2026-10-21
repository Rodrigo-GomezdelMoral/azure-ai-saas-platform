import asyncio
import json
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.config import Settings
from app.models import UsageEvent
from app.quotas import redis_client

GROUP = "drain"
HEARTBEAT = Path("/tmp/alive")
Batch = list[dict[str, Any]]
Sender = Callable[[Batch], Awaitable[None]]


class Meter:
    """The request path writes one stream entry per request and never a billing row."""

    def __init__(self, redis: Redis, stream: str) -> None:
        self.redis, self.stream = redis, stream

    async def record(self, event: UsageEvent) -> str:
        return str(await self.redis.xadd(self.stream, event.fields(), maxlen=100_000, approximate=True))


class Drain:
    """A consumer group reads batches, forwards them to Service Bus, acknowledges, and reclaims dead readers' work."""

    def __init__(self, redis: Redis, stream: str, send: Sender, consumer: str = "", idle_ms: int = 60_000) -> None:
        self.redis, self.stream, self.send, self.idle_ms, self.batch = redis, stream, send, idle_ms, 100
        self.consumer = consumer or socket.gethostname()

    async def prepare(self) -> None:
        try:
            await self.redis.xgroup_create(self.stream, GROUP, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def drain_once(self, block_ms: int = 5_000) -> int:
        stream, group, me, count = self.stream, GROUP, self.consumer, self.batch
        claimed: Any = await self.redis.xautoclaim(stream, group, me, min_idle_time=self.idle_ms, count=count)
        entries: list[tuple[str, dict[str, Any]]] = list(claimed[1])
        if not entries:
            fresh: Any = await self.redis.xreadgroup(group, me, {stream: ">"}, count=count, block=block_ms)
            entries = list(fresh[0][1]) if fresh else []
        if not entries:
            return 0
        await self.send([fields for _, fields in entries])
        await self.redis.xack(self.stream, GROUP, *[entry_id for entry_id, _ in entries])
        return len(entries)

    async def run(self) -> None:
        await self.prepare()
        while True:
            await self.drain_once()
            HEARTBEAT.touch()


def servicebus_sender(bus: ServiceBusClient, queue: str) -> Sender:
    sender = bus.get_queue_sender(queue)

    async def send(batch: Batch) -> None:
        messages = [ServiceBusMessage(json.dumps(fields), content_type="application/json") for fields in batch]
        await sender.send_messages(messages)

    return send


async def main() -> None:
    settings, credential = Settings(), DefaultAzureCredential()
    async with credential, ServiceBusClient(settings.servicebus_namespace, credential) as bus:
        drain = Drain(redis_client(settings), settings.usage_stream, servicebus_sender(bus, settings.usage_queue))
        await drain.run()


if __name__ == "__main__":
    asyncio.run(main())
