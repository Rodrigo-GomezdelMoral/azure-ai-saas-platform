import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from azure.core.exceptions import HttpResponseError
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jwt.algorithms import RSAAlgorithm

from app.config import Settings
from app.models import BillingPeriod, Order, Plan
from app.tenancy import CosmosTenantStore, TenantResolver, digest_of
from ops.chargeback import CostRow, reconcile, render
from ops.onboard import Onboarding, Services
from tests.conftest import AUDIENCE, Keys, tenant
from tests.test_gateway import FakeCredential
from tests.test_metering import MemoryVectorStore

ACCOUNT = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.DocumentDB/databaseAccounts/cosmos"
IDENTITY = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-saasgw"
STUB = {"id": "obj-1", "appId": "client-1", "appRoles": [], "value": [], "status": "Succeeded"}
CALLS: list[str] = []


def cloud(request: httpx.Request) -> httpx.Response:
    CALLS.append(f"{request.method} {request.url.path}")
    return httpx.Response(200, json=STUB)


class Documents:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    async def read_item(self, item: str, partition_key: str, **kwargs: Any) -> dict[str, Any]:
        if (partition_key, item) not in self.items:
            raise CosmosResourceNotFoundError(message="missing")  # type: ignore[no-untyped-call]
        return dict(self.items[(partition_key, item)])

    async def upsert_item(self, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.items[(body["tenantId"], body["id"])] = dict(body)
        return body


class LocalSigner:
    def __init__(self, keys: Keys) -> None:
        self.kid, self.key = "platform-1", keys.platform
        self.jwk = dict(RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)) | {"kid": self.kid}

    async def sign(self, digest: bytes) -> bytes:
        return self.key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))


@pytest.fixture
def services(settings: Settings, keys: Keys, redis: Any, tmp_path: Path) -> Services:
    CALLS.clear()
    extra = {"gateway_client_id": AUDIENCE, "cosmos_account_id": ACCOUNT, "gateway_identity_id": IDENTITY}
    extra |= {"oidc_issuer": "https://issuer.invalid/", "onboard_workdir": str(tmp_path)}
    http = httpx.AsyncClient(transport=httpx.MockTransport(cloud))
    indexes = AsyncMock(create_index=AsyncMock(side_effect=HttpResponseError(message="the index already exists")))
    fakes = (Documents(), Documents(), redis, MemoryVectorStore(), indexes, AsyncMock(), LocalSigner(keys))
    apply = AsyncMock(return_value="applied")
    return Services(settings.model_copy(update=extra), FakeCredential(), http, *fakes, AsyncMock(), apply)  # type: ignore[arg-type]


async def test_business_onboarding_creates_every_artefact_and_a_credential_that_resolves(
    services: Services, keys: Keys, tmp_path: Path
) -> None:
    store = CosmosTenantStore(services.tenants, services.credentials)
    assert await store.signing_keys() == []
    tenant, issued = await Onboarding(services).run(Order(name="Globex", plan=Plan.BUSINESS), log=lambda _: None)
    document = services.tenants.items[("globex", "globex")]  # type: ignore[attr-defined]
    assert len(document["steps"]) == 8 and tenant.client_id == "client-1" and document["status"] == "ready"
    assert "POST /v1.0/servicePrincipals/obj-1/appRoleAssignedTo" in CALLS and "PATCH /v1.0/applications/obj-1" in CALLS
    assert f"PUT {ACCOUNT}/sqlDatabases/saas/containers/usage-globex" in CALLS
    assert f"PUT {IDENTITY}/federatedIdentityCredentials/t-globex" in CALLS
    wrapper = (tmp_path / "t-globex" / "kustomization.yaml").read_text(encoding="utf-8")
    assert '"namespace": "t-globex"' in wrapper and "/t/globex" in wrapper and "overlays/business" in wrapper
    assert "TENANT_SCOPE=globex" in (tmp_path / "t-globex" / "settings.env").read_text(encoding="utf-8")
    assert services.indexes.create_index.await_args.args[0].name == "t-globex"  # type: ignore[attr-defined]
    assert services.indexers.run_indexer.await_args.args == ("t-globex-indexer",)  # type: ignore[attr-defined]
    assert await services.redis.hget("quota:{globex}:bucket", "tokens") == "500"
    resolver = TenantResolver(services.settings, store, keys)
    assert (await resolver.resolve(f"Bearer {issued}")).tenant.id == "globex"  # type: ignore[union-attr]
    stored = json.dumps([*services.tenants.items.values(), *services.credentials.items.values()])  # type: ignore[attr-defined]
    assert issued not in stored and digest_of(issued) in stored


async def test_starter_tenant_gets_a_sealed_credential_and_no_control_plane_calls(services: Services) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = dict(RSAAlgorithm.to_jwk(private.public_key(), as_dict=True))
    order = Order(name="Acme", plan=Plan.STARTER, seal={"n": str(public["n"]), "e": str(public["e"])})
    tenant, issued = await Onboarding(services).run(order, log=lambda _: None)
    assert CALLS == [] and tenant.isolation.namespace == "tenants-starter"
    sealed = base64.b64decode(services.tenants.items[("acme", "acme")]["sealed_credential"])  # type: ignore[attr-defined]
    key = private.decrypt(sealed[:256], padding.OAEP(padding.MGF1(hashes.SHA256()), hashes.SHA256(), None))
    assert AESGCM(key).decrypt(sealed[256:268], sealed[268:], None).decode() == issued


def test_chargeback_reconciles_both_sources_and_explains_the_gap() -> None:
    acme, globex = tenant("acme", Plan.STARTER), tenant("globex", Plan.BUSINESS)
    usage = {"acme": (40, 500), "globex": (900, 1_500)}
    periods = {n: BillingPeriod(tenant_id=n, month="2026-10", requests=r, cents=c) for n, (r, c) in usage.items()}
    rows = [CostRow("Azure Kubernetes Service", "", 120.0), CostRow("Azure Cosmos DB", "globex", 3.0)]
    lines = reconcile([acme, globex], periods, rows)
    assert [round(line.shared_eur, 2) for line in lines] == [30.0, 90.0] and lines[1].attributed_eur == 3.0
    assert round(lines[0].cost_eur, 2) == 35.0 and round(lines[1].cost_eur, 2) == 108.0
    assert round(lines[0].margin, 4) == round((49 - 35) / 49, 4)
    report = render("2026-10", lines, rows)
    assert "| acme | starter | 40 | 5.00 €" in report and "no single tenant causes" in report
