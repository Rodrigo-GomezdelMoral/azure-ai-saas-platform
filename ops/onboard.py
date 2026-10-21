"""Turns a company name and a plan into a serving tenant: eight artefacts in order, each one idempotent.

Runs from a workstation (`python -m ops.onboard --name "Globex Industries" --plan business`) and as the KEDA-scaled
worker behind the console (`--worker`), which drains the Service Bus queue the admin API writes to.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import AzureError, HttpResponseError
from azure.core.messaging import CloudEvent
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.eventgrid.aio import EventGridPublisherClient
from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.keys.aio import KeyClient
from azure.keyvault.keys.crypto import SignatureAlgorithm
from azure.keyvault.keys.crypto.aio import CryptographyClient
from azure.search.documents.indexes.aio import SearchIndexClient, SearchIndexerClient
from azure.servicebus.aio import AutoLockRenewer, ServiceBusClient
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jwt.algorithms import RSAAlgorithm
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.cache import RediSearchStore, VectorStore
from app.config import Settings
from app.models import Credential, Isolation, Order, PiiPolicy, Plan, Tenant
from app.quotas import redis_client
from app.tenancy import CREDENTIAL_ISSUER, JWKS_DOCUMENT, JWKS_PARTITION, digest_of

ARM, GRAPH = "https://management.azure.com", "https://graph.microsoft.com/v1.0"
ARM_SCOPE, GRAPH_SCOPE = f"{ARM}/.default", "https://graph.microsoft.com/.default"
COSMOS_API, IDENTITY_API, EXCHANGE = "2025-10-15", "2024-11-30", "api://AzureADTokenExchange"
PENDING = ("InProgress", "Enqueued", "Accepted", "Running")
DEDICATED = ("gateway_client_id", "gateway_identity_id", "cosmos_account_id", "oidc_issuer")
CREDENTIAL_DAYS = 365
HEARTBEAT = Path("/tmp/alive")
Apply = Callable[[Path], Awaitable[str]]
Log = Callable[[str], None]


class OnboardingError(Exception):
    pass


FAILURES = (OnboardingError, httpx.HTTPError, AzureError, RedisError, OSError, subprocess.CalledProcessError)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def env_value(value: Any) -> str:
    return "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)


class KeyVaultSigner:
    """RS256 with the vault's key: the private half never leaves Key Vault; the gateway verifies with the public JWK."""

    def __init__(self, vault_url: str, key_name: str, credential: AsyncTokenCredential) -> None:
        self.keys, self.key_name = KeyClient(vault_url, credential), key_name
        self.crypto = CryptographyClient(f"{vault_url.rstrip('/')}/keys/{key_name}", credential)
        self.kid, self.jwk = "", dict[str, str]()

    async def load(self) -> "KeyVaultSigner":
        key = await self.keys.get_key(self.key_name)
        material: Any = key.key
        self.kid, modulus, exponent = str(key.properties.version), b64(bytes(material.n)), b64(bytes(material.e))
        self.jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid, "n": modulus, "e": exponent}
        return self

    async def sign(self, digest: bytes) -> bytes:
        return bytes((await self.crypto.sign(SignatureAlgorithm.rs256, digest)).signature)


@dataclass
class Services:
    settings: Settings
    credential: AsyncTokenCredential
    http: httpx.AsyncClient
    tenants: ContainerProxy
    credentials: ContainerProxy
    redis: Redis
    cache: VectorStore
    indexes: SearchIndexClient
    indexers: SearchIndexerClient
    signer: KeyVaultSigner
    events: EventGridPublisherClient
    apply: Apply
    overlays: Path = Path(__file__).resolve().parent.parent / "k8s" / "overlays"


class Onboarding:
    """Every step converges: a redelivered order or a rerun from the shell ends on the same tenant, not a second one."""

    def __init__(self, services: Services) -> None:
        self.s, self.issued = services, ""
        self.steps = [self.register, self.namespace, self.federate, self.index]
        self.steps += [self.store, self.bucket, self.credential, self.notify]

    async def run(self, order: Order, log: Log = print) -> tuple[Tenant, str]:
        s, started, steps, now = self.s.settings, time.monotonic(), dict[str, str](), datetime.now(UTC)
        where = Isolation.for_plan(order.plan, order.tenant_id, s.shared_index, s.cosmos_database)
        tenant = Tenant(
            id=order.tenant_id, name=order.name, plan=order.plan, pii=order.pii, isolation=where, created=now
        )
        tenant.status, self.issued = "provisioning", ""
        missing = [name for name in DEDICATED if not getattr(s, name)]
        if order.plan is not Plan.STARTER and missing:
            raise OnboardingError(f"a dedicated plan needs {', '.join(missing).upper()}")
        await self.save(tenant, steps)
        for step in self.steps:
            began = time.monotonic()
            try:
                steps[step.__name__] = await step(tenant)
            except FAILURES as error:
                steps[step.__name__], tenant.status = f"failed: {error}", "failed"
                await self.save(tenant, steps)
                raise
            log(f"{step.__name__:<10} {time.monotonic() - began:5.1f} s  {steps[step.__name__]}")
            await self.save(tenant, steps)
        tenant.status = "ready"
        await self.save(tenant, steps, seal(self.issued, order.seal) if order.seal else "")
        log(f"{tenant.id} ({tenant.plan.value}) ready in {time.monotonic() - started:.1f} s")
        return tenant, self.issued

    async def save(self, tenant: Tenant, steps: dict[str, str], sealed: str = "") -> None:
        document = tenant.model_dump(mode="json") | {"tenantId": tenant.id, "steps": steps}
        await self.s.tenants.upsert_item(document | ({"sealed_credential": sealed} if sealed else {}))

    async def request(self, scope: str, method: str, url: str, body: dict[str, Any] | None = None) -> httpx.Response:
        token = await self.s.credential.get_token(scope)
        headers = {"Authorization": f"Bearer {token.token}"}
        response = await self.s.http.request(method, url, json=body, headers=headers)
        missing = response.status_code == 404 and method == "GET"
        if not missing and not (response.status_code == 400 and "already exists" in response.text):
            response.raise_for_status()
        return response

    async def graph(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.request(GRAPH_SCOPE, method, f"{GRAPH}/{path}", body)
        return dict(response.json()) if response.is_success and response.content else {}

    async def arm(self, url: str, body: dict[str, Any]) -> None:
        response = await self.request(ARM_SCOPE, "PUT", url, body)
        poll = response.headers.get("azure-asyncoperation", "")
        status = "InProgress" if response.status_code == 202 else "Succeeded"
        while status in PENDING and poll:
            await asyncio.sleep(2)
            status = str((await self.request(ARM_SCOPE, "GET", poll)).json().get("status", "Succeeded"))
        if status != "Succeeded":
            raise OnboardingError(f"{url.split('?')[0]} ended {status}")

    async def register(self, tenant: Tenant) -> str:
        if tenant.plan is Plan.STARTER:
            return "authenticates with the platform credential; no app registration"
        s, value = self.s.settings, f"{self.s.settings.tenant_role_prefix}{tenant.id}"
        gateway = await self.graph("GET", f"applications(appId='{s.api_audience}')")
        roles: list[dict[str, Any]] = list(gateway.get("appRoles", []))
        role = next((item for item in roles if item.get("value") == value), None)
        if role is None:
            role = {"id": str(uuid4()), "value": value, "displayName": value, "isEnabled": True}
            role |= {"allowedMemberTypes": ["Application"], "description": f"Calls the gateway as {tenant.name}"}
            await self.graph("PATCH", f"applications/{gateway['id']}", {"appRoles": [*roles, role]})
        name = f"{tenant.name} regulatory intelligence client"
        safe = name.replace("'", "''")
        listed = await self.graph("GET", f"applications?$filter=displayName eq '{safe}'")
        app = next(iter(listed.get("value", [])), None)
        app = app or await self.graph("POST", "applications", {"displayName": name, "signInAudience": "AzureADMyOrg"})
        principal = await self.graph("GET", f"servicePrincipals(appId='{app['appId']}')")
        principal = principal or await self.graph("POST", "servicePrincipals", {"appId": app["appId"]})
        resource = await self.graph("GET", f"servicePrincipals(appId='{s.api_audience}')")
        grant = {"principalId": principal["id"], "resourceId": resource["id"], "appRoleId": role["id"]}
        await self.graph("POST", f"servicePrincipals/{resource['id']}/appRoleAssignedTo", grant)
        tenant.client_id = str(app["appId"])
        return f"app registration {tenant.client_id} holds the app role {value}"

    async def namespace(self, tenant: Tenant) -> str:
        if tenant.plan is Plan.STARTER:
            return "shared namespace tenants-starter"
        s, folder = self.s.settings, Path(self.s.settings.onboard_workdir) / tenant.isolation.namespace
        folder.mkdir(parents=True, exist_ok=True)
        overlay = os.path.relpath(self.s.overlays / tenant.plan.value, folder).replace("\\", "/")
        image, _, tag = s.gateway_image.rpartition(":")
        route = [{"op": "replace", "path": "/spec/rules/0/matches/0/path/value", "value": f"/t/{tenant.id}"}]
        identity = "/metadata/annotations/azure.workload.identity~1client-id"
        account = [{"op": "replace", "path": identity, "value": s.gateway_client_id}]
        scope = [{"op": "replace", "path": "/spec/template/spec/containers/0/env/0/value", "value": tenant.id}]
        patches = [{"target": {"kind": "HTTPRoute", "name": "gateway"}, "patch": json.dumps(route)}]
        patches += [{"target": {"kind": "ServiceAccount", "name": "gateway"}, "patch": json.dumps(account)}]
        patches += [{"target": {"kind": "Deployment", "name": "gateway"}, "patch": json.dumps(scope)}]
        generator = [{"name": "gateway-settings", "behavior": "replace", "envs": ["settings.env"]}]
        wrapper: dict[str, Any] = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization"}
        wrapper |= {"namespace": tenant.isolation.namespace, "resources": [overlay], "patches": patches}
        wrapper |= {"images": [{"name": "gateway", "newName": image, "newTag": tag}], "configMapGenerator": generator}
        wrapper["generatorOptions"] = {"disableNameSuffixHash": True}
        values = s.model_dump() | {"tenant_scope": tenant.id}
        lines = [f"{key.upper()}={env_value(value)}" for key, value in values.items()]
        (folder / "settings.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (folder / "kustomization.yaml").write_text(json.dumps(wrapper, indent=2) + "\n", encoding="utf-8")
        return await self.s.apply(folder)

    async def federate(self, tenant: Tenant) -> str:
        if tenant.plan is Plan.STARTER:
            return "covered by the shared gateway's federation"
        s, subject = self.s.settings, f"system:serviceaccount:{tenant.isolation.namespace}:gateway"
        url = f"{ARM}{s.gateway_identity_id}/federatedIdentityCredentials/{tenant.isolation.namespace}"
        body = {"properties": {"issuer": s.oidc_issuer, "subject": subject, "audiences": [EXCHANGE]}}
        await self.arm(f"{url}?api-version={IDENTITY_API}", body)
        return f"id-saasgw trusts {subject}"

    async def index(self, tenant: Tenant) -> str:
        if tenant.plan is Plan.STARTER:
            return f"shared index {tenant.isolation.index}, scoped by the gateway"
        shared, name = self.s.settings.shared_index, tenant.isolation.index
        index = await self.s.indexes.get_index(shared)
        index.name = name
        await self.create(self.s.indexes.create_index, index)
        skillset = await self.s.indexers.get_skillset(f"{shared}-skillset")
        skillset.name = f"{name}-skillset"
        for selector in skillset.index_projection.selectors if skillset.index_projection else []:
            selector.target_index_name = name
        await self.create(self.s.indexers.create_skillset, skillset)
        indexer = await self.s.indexers.get_indexer(f"{shared}-indexer")
        indexer.name, indexer.skillset_name, indexer.target_index_name = f"{name}-indexer", skillset.name, name
        await self.create(self.s.indexers.create_indexer, indexer)
        await self.create(self.s.indexers.run_indexer, indexer.name)
        return f"index {name} created and filling from the shared corpus"

    @staticmethod
    async def create(make: Callable[[Any], Awaitable[Any]], definition: Any) -> None:
        try:
            await make(definition)
        except HttpResponseError as error:
            if error.status_code != 409:
                raise

    async def store(self, tenant: Tenant) -> str:
        where = tenant.isolation
        if tenant.plan is Plan.STARTER:
            return f"partition in the shared container {where.database}/{where.container}"
        base = f"{ARM}{self.s.settings.cosmos_account_id}/sqlDatabases/{where.database}"
        if tenant.plan is Plan.ENTERPRISE:
            await self.arm(f"{base}?api-version={COSMOS_API}", {"properties": {"resource": {"id": where.database}}})
        key = {"paths": ["/tenantId", "/month"], "kind": "MultiHash", "version": 2}
        body = {"properties": {"resource": {"id": where.container, "partitionKey": key}}}
        await self.arm(f"{base}/containers/{where.container}?api-version={COSMOS_API}", body)
        return f"{where.database}/{where.container} with hierarchical partition keys"

    async def bucket(self, tenant: Tenant) -> str:
        quota = tenant.quota
        await self.s.redis.hset(f"quota:{tenant.id}:bucket", mapping={"tokens": quota.burst_cents, "at": time.time()})
        await self.s.cache.ensure(tenant.id)
        return f"{quota.burst_cents} cents of burst, {quota.requests_per_minute} a minute, index idx:cache:{tenant.id}"

    async def credential(self, tenant: Tenant) -> str:
        signer, now = self.s.signer, datetime.now(UTC)
        expiry = int((now + timedelta(days=CREDENTIAL_DAYS)).timestamp())
        claims: dict[str, Any] = {"iss": CREDENTIAL_ISSUER, "sub": tenant.id, "aud": self.s.settings.api_audience}
        claims |= {"jti": uuid4().hex, "iat": int(now.timestamp()), "exp": expiry}
        header = {"alg": "RS256", "typ": "JWT", "kid": signer.kid}
        unsigned = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
        self.issued = f"{unsigned}.{b64(await signer.sign(hashlib.sha256(unsigned.encode()).digest()))}"
        record = Credential(tenant_id=tenant.id, kid=signer.kid, digest=digest_of(self.issued), issued=now)
        document = record.model_dump(mode="json") | {"id": record.digest, "tenantId": tenant.id}
        await self.s.credentials.upsert_item(document)
        await self.publish(signer.kid, signer.jwk)
        return f"credential {record.digest[:12]} issued, valid until {datetime.fromtimestamp(expiry, UTC).date()}"

    async def publish(self, kid: str, jwk: dict[str, str]) -> None:
        try:
            document = dict(await self.s.credentials.read_item(item=JWKS_DOCUMENT, partition_key=JWKS_PARTITION))
        except CosmosResourceNotFoundError:
            document = {"id": JWKS_DOCUMENT, "tenantId": JWKS_PARTITION, "keys": []}
        if all(key.get("kid") != kid for key in document["keys"]):
            await self.s.credentials.upsert_item(document | {"keys": [*document["keys"], jwk]})

    async def notify(self, tenant: Tenant) -> str:
        data = {"tenant": tenant.id, "plan": tenant.plan.value, "namespace": tenant.isolation.namespace}
        event = CloudEvent(source="/platform/onboarding", type="saas.tenant.onboarded", subject=tenant.id, data=data)
        await self.s.events.send(event)
        return "tenant.onboarded published to the platform topic"


def seal(secret: str, jwk: dict[str, str]) -> str:
    """AES-GCM over the credential, RSA-OAEP over the AES key: the console opens it in the browser, nothing else can."""
    public = RSAAlgorithm.from_jwk({"kty": "RSA", "n": jwk["n"], "e": jwk["e"]})
    if not isinstance(public, rsa.RSAPublicKey):
        raise OnboardingError("the seal must be an RSA public key")
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    oaep = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    sealed = public.encrypt(key, oaep) + nonce + AESGCM(key).encrypt(nonce, secret.encode(), None)
    return base64.b64encode(sealed).decode()


async def kubectl_apply(folder: Path) -> str:
    render = ["kubectl", "kustomize", "--load-restrictor", "LoadRestrictionsNone", str(folder)]
    rendered = subprocess.run(render, check=True, capture_output=True, text=True).stdout
    applied = subprocess.run(
        ["kubectl", "apply", "-f", "-"], input=rendered, check=True, capture_output=True, text=True
    )
    return f"{len(applied.stdout.splitlines())} objects applied for {folder.name}"


async def worker(onboarding: Onboarding, bus: ServiceBusClient, queue: str) -> None:
    renewer = AutoLockRenewer(max_lock_renewal_duration=900)
    async with bus.get_queue_receiver(queue, auto_lock_renewer=renewer) as receiver:
        while True:
            HEARTBEAT.touch()
            for message in await receiver.receive_messages(max_message_count=1, max_wait_time=30):
                try:
                    await onboarding.run(Order.model_validate(json.loads(str(message))))
                except FAILURES as error:
                    reason = str(error)[:4000]
                    await receiver.dead_letter_message(message, reason="onboarding failed", error_description=reason)
                    continue
                await receiver.complete_message(message)


async def connect(settings: Settings, credential: AsyncTokenCredential, http: httpx.AsyncClient) -> Services:
    database = CosmosClient(settings.cosmos_endpoint, credential).get_database_client(settings.cosmos_database)
    redis = redis_client(settings)
    signer = await KeyVaultSigner(settings.keyvault_url, settings.signing_key_name, credential).load()
    tenants = database.get_container_client(settings.tenants_container)
    credentials = database.get_container_client(settings.credentials_container)
    indexes = SearchIndexClient(settings.search_endpoint, credential)
    indexers = SearchIndexerClient(settings.search_endpoint, credential)
    events = EventGridPublisherClient(settings.eventgrid_endpoint, credential)
    cache = RediSearchStore(redis)
    return Services(
        settings, credential, http, tenants, credentials, redis, cache, indexes, indexers, signer, events, kubectl_apply
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Onboard a tenant, or drain the onboarding queue as the worker")
    parser.add_argument("--name", help="the company name; the tenant id derives from it")
    parser.add_argument("--plan", choices=[plan.value for plan in Plan], default=Plan.STARTER.value)
    parser.add_argument("--pii", choices=[policy.value for policy in PiiPolicy], default=PiiPolicy.NONE.value)
    parser.add_argument("--worker", action="store_true", help="consume orders from the Service Bus queue")
    args, settings, credential = parser.parse_args(), Settings(), DefaultAzureCredential()
    bus = ServiceBusClient(settings.servicebus_namespace, credential)
    async with credential, httpx.AsyncClient(timeout=60) as http, bus:
        onboarding = Onboarding(await connect(settings, credential, http))
        if args.worker:
            await worker(onboarding, bus, settings.onboarding_queue)
        elif args.name:
            order = Order(name=args.name, plan=Plan(args.plan), pii=PiiPolicy(args.pii))
            tenant, issued = await onboarding.run(order)
            print(f"\ncredential for {tenant.id}, shown once and never stored:\n{issued}")
        else:
            raise SystemExit("--name is required unless --worker is given")


if __name__ == "__main__":
    asyncio.run(main())
