import json

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.config import Settings
from app.models import Plan
from app.tenancy import AccessError, TenantMiddleware, TenantResolver
from tests.conftest import Keys, MemoryStore


async def test_entra_app_role_names_the_tenant(resolver: TenantResolver, keys: Keys) -> None:
    principal = await resolver.resolve(f"Bearer {keys.entra_token(['tenant:globex'])}")
    assert principal.tenant is not None and principal.tenant.id == "globex" and principal.tenant.plan is Plan.BUSINESS
    assert not principal.admin


BAD_ROLES = [[], ["tenant:acme", "tenant:globex"], ["tenant:nobody"], ["tenant:initech"]]


@pytest.mark.parametrize("roles", BAD_ROLES)
async def test_bad_roles_are_rejected(resolver: TenantResolver, keys: Keys, roles: list[str]) -> None:
    with pytest.raises(AccessError) as rejected:
        await resolver.resolve(f"Bearer {keys.entra_token(roles)}")
    assert rejected.value.status == 403


async def test_wrong_audience_and_expiry_fail_signature_checks(resolver: TenantResolver, keys: Keys) -> None:
    wrong_audience = keys.entra_token(["tenant:acme"], audience="someone-else")
    for token in (wrong_audience, keys.entra_token(["tenant:acme"], expired=True)):
        with pytest.raises(AccessError) as rejected:
            await resolver.resolve(f"Bearer {token}")
        assert rejected.value.status == 401


async def test_admin_role_yields_an_admin_without_tenant(resolver: TenantResolver, keys: Keys) -> None:
    principal = await resolver.resolve(f"Bearer {keys.entra_token(['Platform.Admin'])}")
    assert principal.admin and principal.tenant is None
    with pytest.raises(AccessError):
        principal.require_tenant()


async def test_signed_credential_names_its_subject(resolver: TenantResolver, store: MemoryStore) -> None:
    principal = await resolver.resolve(f"Bearer {store.issue('acme')}")
    assert principal.tenant is not None and principal.tenant.id == "acme"
    assert principal.subject.startswith("credential:")


async def test_bad_credentials_are_rejected(resolver: TenantResolver, store: MemoryStore, keys: Keys) -> None:
    revoked = store.issue("acme")
    store.revoke("acme", revoked)
    forged, unknown = keys.credential("acme", key=keys.rogue), keys.credential("acme")
    for token in (forged, unknown, revoked, keys.credential("acme", kid="other")):
        with pytest.raises(AccessError) as rejected:
            await resolver.resolve(f"Bearer {token}")
        assert rejected.value.status == 401


async def test_a_dedicated_gateway_refuses_other_tenants(settings: Settings, store: MemoryStore, keys: Keys) -> None:
    scoped = TenantResolver(settings.model_copy(update={"tenant_scope": "globex"}), store, keys)
    assert (await scoped.resolve(f"Bearer {keys.entra_token(['tenant:globex'])}")).tenant is not None
    with pytest.raises(AccessError) as rejected:
        await scoped.resolve(f"Bearer {keys.entra_token(['tenant:acme'])}")
    assert rejected.value.status == 403
    starter_only = TenantResolver(settings.model_copy(update={"tenant_scope": "starter"}), store, keys)
    with pytest.raises(AccessError):
        await starter_only.resolve(f"Bearer {keys.entra_token(['tenant:globex'])}")


def test_middleware_rejects_before_routing(resolver: TenantResolver, keys: Keys, store: MemoryStore) -> None:
    async def whoami(request):  # type: ignore[no-untyped-def]
        return PlainTextResponse(request.state.principal.tenant.id)

    async def healthz(request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/v1/whoami", whoami), Route("/healthz", healthz)])
    client = TestClient(TenantMiddleware(app, resolver))
    store.reads.clear()
    assert client.get("/healthz").text == "ok"
    rejected = client.get("/v1/whoami")
    assert rejected.status_code == 401 and rejected.headers["content-type"].startswith("application/problem+json")
    assert json.loads(rejected.text)["instance"] == "/v1/whoami" and rejected.headers["www-authenticate"] == "Bearer"
    assert store.reads == []
    accepted = client.get("/v1/whoami", headers={"Authorization": f"Bearer {keys.entra_token(['tenant:acme'])}"})
    assert accepted.text == "acme"
