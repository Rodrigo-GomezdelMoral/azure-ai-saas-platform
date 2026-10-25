import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt
from azure.ai.textanalytics.aio import TextAnalyticsClient
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import telemetry
from app.cache import Probe, RediSearchStore, SemanticCache, foundry_embedder
from app.config import Settings
from app.metering import Meter
from app.models import Order, Tenant, UsageEvent
from app.privacy import Prepared, Privacy, RedactionError, Redactor
from app.quotas import QuotaGate, Reservation, redis_client
from app.tenancy import LOGIN, AccessError, CosmosTenantStore, Principal, TenantMiddleware, TenantResolver, problem
from app.upstream import Exchange, UpstreamClient, UpstreamError

routes = APIRouter()
CONSOLE = Path(__file__).resolve().parent.parent / "console" / "dist"


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    conversation_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9-]{1,64}$")


@dataclass
class Gateway:
    settings: Settings
    resolver: TenantResolver
    quotas: QuotaGate
    cache: SemanticCache
    privacy: Privacy
    upstream: UpstreamClient
    meter: Meter
    tenants: ContainerProxy | None = None
    onboard: Any = None


async def build(settings: Settings) -> Gateway:
    credential, redis, http = DefaultAzureCredential(), redis_client(settings), httpx.AsyncClient()
    cosmos = CosmosClient(settings.cosmos_endpoint, credential).get_database_client(settings.cosmos_database)
    tenants = cosmos.get_container_client(settings.tenants_container)
    store = CosmosTenantStore(tenants, cosmos.get_container_client(settings.credentials_container))
    keys = jwt.PyJWKClient(f"{LOGIN}/{settings.entra_tenant_id}/discovery/v2.0/keys", cache_keys=True)
    resolver = TenantResolver(settings, store, keys)
    await resolver.refresh_keys()
    embedder = foundry_embedder(http, settings, credential)
    cache = SemanticCache(RediSearchStore(redis), embedder, settings.cache_similarity)
    language = TextAnalyticsClient(settings.foundry_endpoint, credential)
    privacy = Privacy(Redactor(language, settings.pii_document_characters, settings.pii_documents_per_request))
    upstream = UpstreamClient(http, settings, credential)
    bus = ServiceBusClient(settings.servicebus_namespace, credential).get_queue_sender(settings.onboarding_queue)
    meter = Meter(redis, settings.usage_stream)
    return Gateway(settings, resolver, QuotaGate(redis), cache, privacy, upstream, meter, tenants, bus)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    telemetry.configure(settings)
    app.state.gateway = await build(settings)
    yield


def create_app(gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="Regulatory Intelligence as a Service", lifespan=None if gateway else lifespan)
    if gateway:
        app.state.gateway = gateway
    for error_type in (AccessError, HTTPException, RequestValidationError, UpstreamError, RedactionError):
        app.add_exception_handler(error_type, rejected)
    app.include_router(routes)
    app.add_middleware(TenantMiddleware)
    if CONSOLE.is_dir():
        app.mount("/console", StaticFiles(directory=CONSOLE, html=True), name="console")
    return app


async def rejected(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, UpstreamError):
        error = AccessError(503, error.detail, error.retry_after)
    elif isinstance(error, RedactionError):
        error = AccessError(502, f"pii redaction unavailable: {error}")
    elif isinstance(error, HTTPException):
        error = AccessError(error.status_code, str(error.detail))
    elif not isinstance(error, AccessError):
        error = AccessError(422, str(error))
    return problem(error, request.url.path)


def principal(request: Request) -> Principal:
    found: Principal = request.scope["state"]["principal"]
    return found


def usage_event(tenant: Tenant, cents: float, cached: bool, redacted: bool, upstream_ms: int) -> UsageEvent:
    where = {"database": tenant.isolation.database, "container": tenant.isolation.container}
    trace_id, occurred = telemetry.current_trace_id(), datetime.now(UTC)
    common = {"tenant_id": tenant.id, "trace_id": trace_id, "cents": cents, "cached": cached, "redacted": redacted}
    return UsageEvent(upstream_ms=upstream_ms, occurred=occurred, **common, **where)


@routes.post("/v1/answer")
async def answer(body: Question, request: Request) -> StreamingResponse:
    gateway: Gateway = request.app.state.gateway
    tenant = principal(request).require_tenant()
    telemetry.TENANT.set(tenant.id)
    reservation = await reserve(gateway, tenant)
    try:
        prepared = await gateway.privacy.prepare(tenant, body.question)
        probe = await gateway.cache.lookup(tenant.id, prepared.stored)
        if probe.answer is not None:
            await release(gateway, reservation)
            await gateway.meter.record(usage_event(tenant, 0, True, prepared.entities > 0, 0))
            cached = f"event: verify\ndata: {json.dumps(probe.answer)}\n\n"
            return StreamingResponse(iter([cached]), media_type="text/event-stream", headers={"x-cache": "hit"})
        exchange = await gateway.upstream.answer(prepared.upstream, tenant.isolation.index, body.conversation_id)
    except (RedactionError, UpstreamError):
        await release(gateway, reservation)
        raise
    events = relay(gateway, tenant, reservation, prepared, probe, exchange)
    return StreamingResponse(events, media_type="text/event-stream", headers={"x-cache": "miss"})


async def reserve(gateway: Gateway, tenant: Tenant) -> Reservation | None:
    """Quota before work; enforcement is switched off only for the noisy-neighbour comparison and the baseline."""
    if not gateway.settings.enforce_quota:
        return None
    decision, reservation = await gateway.quotas.reserve(tenant.id, tenant.quota)
    if not decision.allowed or reservation is None:
        raise AccessError(429, f"quota exhausted: {decision.reason}", decision.retry_after)
    return reservation


async def release(gateway: Gateway, held: Reservation | None) -> None:
    if held is not None:
        await gateway.quotas.release(held)


async def relay(
    gateway: Gateway, tenant: Tenant, held: Reservation | None, prepared: Prepared, probe: Probe, exchange: Exchange
) -> AsyncIterator[str]:
    """Proxies the upstream events unchanged, then settles the quota, meters the cost and remembers the answer."""
    started = time.monotonic()
    try:
        async for chunk in exchange.events():
            yield chunk
    except UpstreamError:
        await release(gateway, held)
        return
    final, elapsed = exchange.final or {}, int((time.monotonic() - started) * 1000)
    answer = final.get("data") or final
    cents = float(answer.get("cost_eur", 0.0)) * 100
    if held is not None:
        await gateway.quotas.settle(held, cents)
    await gateway.meter.record(usage_event(tenant, cents, False, prepared.entities > 0, elapsed))
    if final and not answer.get("refusal"):
        await gateway.cache.remember(tenant.id, prepared.stored, final, probe.vector)


@routes.get("/v1/usage")
async def usage(request: Request) -> dict[str, Any]:
    gateway: Gateway = request.app.state.gateway
    tenant, month = principal(request).require_tenant(), datetime.now(UTC).strftime("%Y-%m")
    spent = await gateway.quotas.spent_cents(tenant.id, month)
    period = {"tenant": tenant.id, "plan": tenant.plan, "month": month}
    return period | {"spent_cents": round(spent, 4), "monthly_cents": tenant.quota.monthly_cents}


def admin(request: Request) -> Gateway:
    if not principal(request).admin:
        raise AccessError(403, "the platform admin role is required")
    gateway: Gateway = request.app.state.gateway
    return gateway


@routes.post("/admin/tenants", status_code=202)
async def onboard(body: Order, request: Request) -> dict[str, str]:
    gateway = admin(request)
    order = {"tenant_id": body.tenant_id, "requested": datetime.now(UTC).isoformat()} | body.model_dump(mode="json")
    await gateway.onboard.send_messages(ServiceBusMessage(json.dumps(order), content_type="application/json"))
    return {"tenant": body.tenant_id, "status": "queued"}


@routes.get("/admin/tenants")
async def tenants(request: Request) -> list[dict[str, Any]]:
    gateway = admin(request)
    if gateway.tenants is None:
        return []
    fields = "c.id, c.name, c.plan, c.pii, c.isolation, c.created, c.suspended, c.status"
    return [dict(item) async for item in gateway.tenants.query_items(f"SELECT {fields} FROM c")]


@routes.get("/admin/tenants/{tenant_id}")
async def tenant_status(tenant_id: str, request: Request) -> dict[str, Any]:
    """The onboarding's progress; a sealed credential is returned once and removed from the document on that read."""
    gateway = admin(request)
    if gateway.tenants is None:
        raise AccessError(404, "unknown tenant")
    try:
        document = dict(await gateway.tenants.read_item(item=tenant_id, partition_key=tenant_id))
    except CosmosResourceNotFoundError as missing:
        raise AccessError(404, "unknown tenant") from missing
    if document.get("sealed_credential"):
        forget = [{"op": "remove", "path": "/sealed_credential"}]
        await gateway.tenants.patch_item(item=tenant_id, partition_key=tenant_id, patch_operations=forget)
    return {key: value for key, value in document.items() if not key.startswith("_")}


@routes.get("/console/config.json")
async def console_config(request: Request) -> dict[str, str]:
    settings: Settings = request.app.state.gateway.settings
    return {"tenant_id": settings.entra_tenant_id, "client_id": settings.api_audience}


@routes.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    gateway: Gateway = request.app.state.gateway
    try:
        await gateway.quotas.redis.ping()
    except (OSError, ConnectionError) as error:
        return JSONResponse({"status": "degraded", "redis": type(error).__name__}, 503)
    return JSONResponse({"status": "ok"})


app = create_app()
