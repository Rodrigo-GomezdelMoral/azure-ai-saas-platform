"""The thesis test: a request authenticated as tenant A can never reach tenant B's data, in any store.

Each case names a store and probes it with A's credentials for B's record. New stores are added as the phases
build them; the test stays red for any store whose probe can see across the tenant boundary.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.models import PLANS
from app.quotas import QuotaGate
from app.tenancy import TenantResolver, digest_of
from tests.conftest import Keys, MemoryStore

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


STORES: dict[str, Probe] = {"tenants": tenant_record, "credentials": credential_record, "quota": quota_bucket}


@pytest.fixture
def world(resolver: TenantResolver, store: MemoryStore, keys: Keys, redis: Any, tenants: list[Any]) -> dict[str, Any]:
    return {"resolver": resolver, "store": store, "keys": keys, "gate": QuotaGate(redis), "tenants": tenants}


@pytest.mark.parametrize("store_name", sorted(STORES))
async def test_tenant_a_cannot_reach_tenant_b(store_name: str, world: dict[str, Any]) -> None:
    assert not await STORES[store_name](world), f"tenant A reached tenant B's data in {store_name}"
