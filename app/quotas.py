import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis.asyncio import Redis

from app.models import Quota

MONTH_KEY_TTL = 40 * 24 * 3600

RESERVE = """
local bucket, minute, month = KEYS[1], KEYS[2], KEYS[3]
local now, rate, capacity, cost = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local rpm, monthly = tonumber(ARGV[5]), tonumber(ARGV[6])
local state = redis.call('HMGET', bucket, 'tokens', 'at')
local tokens = math.min(capacity, (tonumber(state[1]) or capacity) + (now - (tonumber(state[2]) or now)) * rate)
local spent = tonumber(redis.call('GET', month) or '0')
if spent + cost > monthly then return {0, 'monthly', 0, math.floor(tokens)} end
if tokens < cost then return {0, 'burst', math.ceil((cost - tokens) / rate), math.floor(tokens)} end
local calls = redis.call('INCR', minute)
if calls == 1 then redis.call('EXPIRE', minute, 60) end
if calls > rpm then return {0, 'rate', math.max(redis.call('TTL', minute), 1), math.floor(tokens)} end
redis.call('HSET', bucket, 'tokens', tokens - cost, 'at', now)
return {1, 'ok', 0, math.floor(tokens - cost)}
"""

SETTLE = """
local bucket, month = KEYS[1], KEYS[2]
local reserved, actual, capacity = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local tokens = tonumber(redis.call('HGET', bucket, 'tokens') or capacity)
redis.call('HSET', bucket, 'tokens', math.min(capacity, tokens + reserved - actual))
if actual > 0 then redis.call('INCRBY', month, actual) redis.call('EXPIRE', month, ARGV[4]) end
return 1
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    retry_after: int
    remaining_cents: int


@dataclass(frozen=True)
class Reservation:
    tenant_id: str
    cents: int
    capacity: int
    month: str


class QuotaGate:
    """A token bucket in euro cents per tenant, checked before any work and reconciled with the measured cost."""

    def __init__(self, redis: Redis, clock: Callable[[], Awaitable[float]] | None = None) -> None:
        self.redis, self.clock = redis, clock or self._server_time
        self._reserve, self._settle = redis.register_script(RESERVE), redis.register_script(SETTLE)

    async def _server_time(self) -> float:
        seconds, micros = await self.redis.time()
        return float(seconds) + float(micros) / 1_000_000

    async def reserve(self, tenant_id: str, q: Quota) -> tuple[Decision, Reservation | None]:
        now = await self.clock()
        month, minute = time.strftime("%Y-%m", time.gmtime(now)), int(now // 60)
        keys = [f"quota:{tenant_id}:bucket", f"quota:{tenant_id}:rpm:{minute}", f"quota:{tenant_id}:month:{month}"]
        args = [now, q.refill_per_second, q.burst_cents, q.estimated_cents, q.requests_per_minute, q.monthly_cents]
        allowed, reason, retry_after, remaining = await self._reserve(keys=keys, args=args)
        decision = Decision(bool(allowed), str(reason), int(retry_after), int(remaining))
        reservation = Reservation(tenant_id, q.estimated_cents, q.burst_cents, month) if decision.allowed else None
        return decision, reservation

    async def settle(self, reservation: Reservation, actual_cents: int) -> None:
        keys = [f"quota:{reservation.tenant_id}:bucket", f"quota:{reservation.tenant_id}:month:{reservation.month}"]
        await self._settle(keys=keys, args=[reservation.cents, actual_cents, reservation.capacity, MONTH_KEY_TTL])

    async def release(self, reservation: Reservation) -> None:
        await self.settle(reservation, 0)

    async def spent_cents(self, tenant_id: str, month: str) -> int:
        return int(await self.redis.get(f"quota:{tenant_id}:month:{month}") or 0)
