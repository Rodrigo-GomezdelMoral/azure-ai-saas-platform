"""The thesis test: a request authenticated as tenant A can never reach tenant B's data, in any store.

Each case names a store and probes it with A's credentials for B's record. New stores are added as the phases
build them; the test stays red for any store whose probe can see across the tenant boundary.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.cache import RediSearchStore, SemanticCache
from app.models import PLANS
from app.quotas import QuotaGate
from app.tenancy import TenantResolver, digest_of
from functions.aggregate import aggregate, apply
from tests.conftest import MemoryStore
from tests.test_gateway import World
from tests.test_metering import FakeContainer, MemoryVectorStore, event, fake_embedder

Probe = Callable[[dict[str, Any]], Awaitable[bool]]


async def tenant_record(world: dict[str, Any]) -> bool:
    resolver: TenantResolver = world["resolver"]
    principal = await resolver.resolve(f"Bearer {world['keys'].entra_token(['tenant:acme'])}")
    return principal.tenant is not None and principal.tenant.id == "globex"


async def credential_record(world: dict[str, Any]) -> bool:
    store: MemoryStore = world["store"]
    token = store.issue("globex")
    return await store.credential_revoked("acme", digest_of(token)) is not None


async def quota_bucket(world: dict[str, Any]) -> bool:
    gate: QuotaGate = world["gate"]
    quota = PLANS[world["tenants"][0].plan]
    before = (await gate.reserve("globex", quota))[0].remaining_cents
    for _ in range(3):
        await gate.reserve("acme", quota)
    after = (await gate.reserve("globex", quota))[0].remaining_cents
    return after != before - quota.estimated_cents


async def cached_answer(world: dict[str, Any]) -> bool:
    cache = SemanticCache(MemoryVectorStore(), fake_embedder(), similarity=0.5)
    probe = await cache.lookup("globex", "What does Article 10 require?")
    await cache.remember("globex", "What does Article 10 require?", {"claims": ["globex only"]}, probe.vector)
    keyed = RediSearchStore(world["redis"])
    await keyed.put("globex", "d1", "question", '{"claims": []}', probe.vector)
    leaked_keys = [key for key in await world["redis"].keys("cache:*") if not key.startswith(keyed.prefix("globex"))]
    return (await cache.lookup("acme", "What does Article 10 require?")).answer is not None or bool(leaked_keys)


async def usage_record(world: dict[str, Any]) -> bool:
    container = FakeContainer()
    for (_, _, tenant_id, month), delta in aggregate([event("globex", 5).fields()]).items():
        apply(container, tenant_id, month, delta)
    seen = {partition for partition, _, _ in container.documents}
    return "acme" in seen or ("globex", "2026-11", "2026-11") not in container.documents


async def upstream_index(world: dict[str, Any]) -> bool:
    gateway = World(world["settings"], world["store"], world["keys"], world["redis"])
    await gateway.resolver.refresh_keys()
    await gateway.ask("acme")
    sent, tenants = gateway.upstream.requests[0]["index"], world["store"].tenants
    return bool(sent != tenants["acme"].isolation.index or sent == tenants["globex"].isolation.index)


STORES: dict[str, Probe] = {"tenants": tenant_record, "credentials": credential_record, "quota": quota_bucket}
STORES |= {"cache": cached_answer, "usage": usage_record, "index": upstream_index}


@pytest.fixture
def world(resolver: Any, store: Any, keys: Any, redis: Any, tenants: Any, settings: Any) -> dict[str, Any]:
    stores = {"resolver": resolver, "store": store, "keys": keys, "gate": QuotaGate(redis)}
    return stores | {"tenants": tenants, "redis": redis, "settings": settings}


@pytest.mark.parametrize("store_name", sorted(STORES))
async def test_tenant_a_cannot_reach_tenant_b(store_name: str, world: dict[str, Any]) -> None:
    assert not await STORES[store_name](world), f"tenant A reached tenant B's data in {store_name}"
