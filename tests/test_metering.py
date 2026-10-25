import hashlib
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from app.cache import Probe, RediSearchStore, SemanticCache
from app.metering import Batch, Drain, Meter
from app.models import UsageEvent
from functions.aggregate import aggregate, apply

MONTH = datetime(2026, 11, 1, tzinfo=UTC)


def event(tenant_id: str, cents: int, day: int = 0, cached: bool = False) -> UsageEvent:
    occurred = MONTH + timedelta(days=day, seconds=random.randrange(86_400))
    container, trace_id = "usage" if tenant_id == "acme" else f"usage-{tenant_id}", f"t-{random.randrange(1 << 48):x}"
    fields = {"tenant_id": tenant_id, "trace_id": trace_id, "database": "saas", "container": container}
    return UsageEvent(cents=cents, cached=cached, upstream_ms=1200, occurred=occurred, **fields)


class Collector:
    def __init__(self) -> None:
        self.batches: list[Batch] = []

    async def __call__(self, batch: Batch) -> None:
        self.batches.append(batch)

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [fields for batch in self.batches for fields in batch]


class FakeContainer:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str, str], dict[str, Any]] = {}

    def patch_item(self, item: str, partition_key: list[str], patch_operations: list[dict[str, Any]]) -> None:
        key = (partition_key[0], partition_key[1], item)
        if key not in self.documents:
            raise CosmosResourceNotFoundError(message="missing")  # type: ignore[no-untyped-call]
        for operation in patch_operations:
            name, value = operation["path"].lstrip("/"), operation["value"]
            self.documents[key][name] = self.documents[key].get(name, 0) + value if operation["op"] == "incr" else value

    def create_item(self, body: dict[str, Any]) -> None:
        self.documents[(body["tenantId"], body["month"], body["id"])] = dict(body)


async def test_request_path_writes_one_stream_entry_that_round_trips(redis: Any) -> None:
    meter, recorded = Meter(redis, "usage"), event("acme", 3, cached=True)
    await meter.record(recorded)
    assert await redis.xlen("usage") == 1
    _, fields = (await redis.xrange("usage"))[0]
    assert UsageEvent.model_validate(fields) == recorded


async def test_drain_forwards_batches_and_acknowledges(redis: Any) -> None:
    meter, collector = Meter(redis, "usage"), Collector()
    for index in range(250):
        await meter.record(event("acme", index % 5))
    drain = Drain(redis, "usage", collector, consumer="one", idle_ms=0)
    await drain.prepare()
    assert [await drain.drain_once(block_ms=1) for _ in range(4)] == [100, 100, 50, 0]
    assert len(collector.messages) == 250 and (await redis.xpending("usage", "drain"))["pending"] == 0


async def test_entries_a_dead_consumer_left_pending_are_reclaimed(redis: Any) -> None:
    meter, lost, collector = Meter(redis, "usage"), Collector(), Collector()
    dead = Drain(redis, "usage", lost, consumer="dead", idle_ms=0)
    await dead.prepare()
    for _ in range(3):
        await meter.record(event("globex", 2))
    await redis.xreadgroup("drain", "dead", {"usage": ">"}, count=10)
    assert await Drain(redis, "usage", collector, consumer="alive", idle_ms=0).drain_once(block_ms=1) == 3
    assert len(collector.messages) == 3 and lost.messages == []


async def test_metering_accuracy_over_a_simulated_month(redis: Any) -> None:
    """Recorded equals consumed exactly; one redelivered batch overcounts by at most that batch, under 2 percent."""
    random.seed(7)
    meter, collector, consumed = Meter(redis, "usage"), Collector(), dict[str, float]()
    for day in range(30):
        for tenant_id, calls in (("acme", 20), ("globex", 90), ("initech", 300)):
            for _ in range(random.randrange(calls // 2, calls)):
                recorded = event(tenant_id, random.choice((1, 2, 2, 3, 5)), day=day)
                consumed[tenant_id] = consumed.get(tenant_id, 0) + recorded.cents
                await meter.record(recorded)
    drain = Drain(redis, "usage", collector, consumer="one", idle_ms=0)
    await drain.prepare()
    while await drain.drain_once(block_ms=1):
        pass
    container = FakeContainer()
    for (_, _, tenant_id, month), delta in aggregate(collector.messages).items():
        apply(container, tenant_id, month, delta)
    billed = {key[0]: document["cents"] for key, document in container.documents.items()}
    assert billed == consumed and all(document["month"] == "2026-11" for document in container.documents.values())
    redelivered = collector.batches[0]
    for (_, _, tenant_id, month), delta in aggregate(redelivered).items():
        apply(container, tenant_id, month, delta)
    overcount = sum(document["cents"] for document in container.documents.values()) - sum(consumed.values())
    assert 0 < overcount <= sum(float(fields["cents"]) for fields in redelivered)
    assert overcount / sum(consumed.values()) < 0.02


def fake_embedder(seed: str = "") -> Any:
    async def embed(text: str) -> list[float]:
        digest = hashlib.sha256(f"{seed}{text.lower().strip()}".encode()).digest()
        return [math.sin(byte) for byte in digest]

    return embed


class MemoryVectorStore:
    def __init__(self) -> None:
        self.entries: dict[str, list[tuple[str, list[float]]]] = {}

    async def ensure(self, tenant_id: str) -> None:
        self.entries.setdefault(tenant_id, [])

    async def nearest(self, tenant_id: str, vector: list[float]) -> tuple[str, float] | None:
        candidates = self.entries.get(tenant_id, [])
        if not candidates:
            return None
        answer, stored = min(candidates, key=lambda item: distance(item[1], vector))
        return answer, distance(stored, vector)

    async def put(self, tenant_id: str, digest: str, question: str, answer: str, vector: list[float]) -> None:
        self.entries.setdefault(tenant_id, []).append((answer, vector))


def distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return 1 - dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def test_cache_hits_only_above_the_similarity_threshold() -> None:
    cache = SemanticCache(MemoryVectorStore(), fake_embedder(), similarity=0.99)
    miss = await cache.lookup("acme", "What does Article 10 require?")
    assert miss.answer is None
    await cache.remember("acme", "What does Article 10 require?", {"claims": ["data governance"]}, miss.vector)
    hit = await cache.lookup("acme", "what does article 10 require? ")
    assert hit.answer == {"claims": ["data governance"]} and hit.similarity >= 0.99
    assert (await cache.lookup("acme", "What does Article 11 require?")).answer is None


async def test_redisearch_keys_and_index_carry_the_tenant(redis: Any) -> None:
    store = RediSearchStore(redis)
    assert store.index("acme") == "idx:cache:acme" and store.prefix("acme") == "cache:acme:"
    await store.put("acme", "d1", "question", '{"claims": []}', [0.1, 0.2])
    assert await redis.keys("cache:acme:*") == ["cache:acme:d1"] and await redis.keys("cache:globex:*") == []
    assert isinstance(await store.nearest("acme", [0.1, 0.2]), type(None)) and isinstance(Probe([0.0]), Probe)
