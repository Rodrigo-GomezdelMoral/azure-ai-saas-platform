import time
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fakeredis import aioredis
from jwt.algorithms import RSAAlgorithm

from app.config import Settings
from app.models import Isolation, Plan, Tenant
from app.tenancy import CREDENTIAL_ISSUER, JWKS_DOCUMENT, TenantResolver, digest_of

AUDIENCE = "00000000-0000-0000-0000-000000000000"


class Keys:
    def __init__(self) -> None:
        self.entra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("Key", (), {"key": self.entra.public_key()})()

    def jwks(self) -> list[dict[str, Any]]:
        jwk = RSAAlgorithm.to_jwk(self.platform.public_key(), as_dict=True)
        return [dict(jwk) | {"kid": "platform-1"}]

    def entra_token(self, roles: list[str], audience: str = AUDIENCE, expired: bool = False) -> str:
        now = int(time.time())
        iss = f"https://login.microsoftonline.com/{AUDIENCE}/v2.0"
        claims = {"iss": iss, "aud": audience, "oid": "user-1", "roles": roles, "iat": now}
        claims["exp"] = now - 10 if expired else now + 600
        return jwt.encode(claims, self.entra, algorithm="RS256")

    def credential(self, tenant_id: str, key: rsa.RSAPrivateKey | None = None, kid: str = "platform-1") -> str:
        claims = {"iss": CREDENTIAL_ISSUER, "aud": AUDIENCE, "sub": tenant_id, "iat": int(time.time())}
        return jwt.encode(claims, key or self.platform, algorithm="RS256", headers={"kid": kid})


class MemoryStore:
    def __init__(self, keys: Keys, tenants: list[Tenant]) -> None:
        self.keys, self.tenants = keys, {tenant.id: tenant for tenant in tenants}
        self.credentials: dict[tuple[str, str], dict[str, Any]] = {}
        self.reads: list[tuple[str, str]] = []

    def issue(self, tenant_id: str) -> str:
        token = self.keys.credential(tenant_id)
        self.credentials[(tenant_id, digest_of(token))] = {"tenantId": tenant_id, "revoked": None}
        return token

    def revoke(self, tenant_id: str, token: str) -> None:
        self.credentials[(tenant_id, digest_of(token))]["revoked"] = datetime.now(UTC).isoformat()

    async def tenant(self, tenant_id: str) -> Tenant | None:
        self.reads.append(("tenants", tenant_id))
        return self.tenants.get(tenant_id)

    async def credential_revoked(self, tenant_id: str, digest: str) -> bool | None:
        self.reads.append(("credentials", tenant_id))
        record = self.credentials.get((tenant_id, digest))
        return None if record is None else record["revoked"] is not None

    async def signing_keys(self) -> list[dict[str, Any]]:
        self.reads.append(("credentials", JWKS_DOCUMENT))
        return self.keys.jwks()


def tenant(tenant_id: str, plan: Plan, suspended: bool = False) -> Tenant:
    isolation = Isolation.for_plan(plan, tenant_id, shared_index="regulations", database="saas")
    created, name = datetime(2026, 10, 1, tzinfo=UTC), f"{tenant_id.title()} Ltd"
    return Tenant(id=tenant_id, name=name, plan=plan, isolation=isolation, created=created, suspended=suspended)


@pytest.fixture
def settings() -> Settings:
    fields = {"entra_tenant_id": AUDIENCE, "api_audience": AUDIENCE, "upstream_audience": f"api://{AUDIENCE}"}
    endpoints = ("redis_host", "cosmos_endpoint", "servicebus_namespace", "eventgrid_endpoint", "foundry_endpoint")
    for name in (*endpoints, "search_endpoint", "keyvault_url", "upstream_url"):
        fields[name] = f"https://{name}.invalid"
    return Settings.model_validate(fields)


@pytest.fixture
def keys() -> Keys:
    return Keys()


@pytest.fixture
def tenants() -> list[Tenant]:
    suspended = tenant("initech", Plan.ENTERPRISE, suspended=True)
    return [tenant("acme", Plan.STARTER), tenant("globex", Plan.BUSINESS), suspended]


@pytest.fixture
def store(keys: Keys, tenants: list[Tenant]) -> MemoryStore:
    return MemoryStore(keys, tenants)


@pytest.fixture
async def resolver(settings: Settings, store: MemoryStore, keys: Keys) -> TenantResolver:
    resolver = TenantResolver(settings, store, keys)
    await resolver.refresh_keys()
    return resolver


@pytest.fixture
async def redis() -> Any:
    client = aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()
