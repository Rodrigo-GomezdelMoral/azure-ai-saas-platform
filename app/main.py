import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import jwt
from azure.ai.textanalytics.aio import TextAnalyticsClient
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import telemetry
from app.cache import Probe, RediSearchStore, SemanticCache, foundry_embedder
from app.config import Settings
from app.metering import Meter
from app.models import PiiPolicy, Plan, Tenant, UsageEvent, tenant_id_from
from app.privacy import Prepared, Privacy, RedactionError, Redactor
from app.quotas import QuotaGate, Reservation, redis_client
from app.tenancy import LOGIN, AccessError, CosmosTenantStore, Principal, TenantMiddleware, TenantResolver, problem
from app.upstream import Exchange, UpstreamClient, UpstreamError

routes = APIRouter()


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    conversation_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9-]{1,64}$")


class Onboarding(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    plan: Plan
    pii: PiiPolicy = PiiPolicy.NONE


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


class Later:
    """The middleware is attached before the clients exist; it resolves through the gateway the lifespan builds."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def resolve(self, authorization: str | None) -> Principal:
        resolver: TenantResolver = self.app.state.gateway.resolver
        return await resolver.resolve(authorization)


def create_app(gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="Regulatory Intelligence as a Service", lifespan=None if gateway else lifespan)
    if gateway:
        app.state.gateway = gateway
    for error_type in (AccessError, HTTPException, RequestValidationError, UpstreamError, RedactionError):
        app.add_exception_handler(error_type, rejected)
    app.include_router(routes)
    app.add_middleware(TenantMiddleware, resolver=Later(app))
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


def usage_event(tenant: Tenant, cents: int, cached: bool, redacted: bool, upstream_ms: int) -> UsageEvent:
    where = {"database": tenant.isolation.database, "container": tenant.isolation.container}
    trace_id, occurred = telemetry.current_trace_id(), datetime.now(UTC)
    common = {"tenant_id": tenant.id, "trace_id": trace_id, "cents": cents, "cached": cached, "redacted": redacted}
    return UsageEvent(upstream_ms=upstream_ms, occurred=occurred, **common, **where)


@routes.post("/v1/answer")
async def answer(body: Question, request: Request) -> StreamingResponse:
    gateway: Gateway = request.app.state.gateway
    tenant = principal(request).require_tenant()
    telemetry.TENANT.set(tenant.id)
    decision, reservation = await gateway.quotas.reserve(tenant.id, tenant.quota)
    if not decision.allowed or reservation is None:
        raise AccessError(429, f"quota exhausted: {decision.reason}", decision.retry_after)
    try:
        prepared = await gateway.privacy.prepare(tenant, body.question)
        probe = await gateway.cache.lookup(tenant.id, prepared.stored)
        if probe.answer is not None:
            await gateway.quotas.release(reservation)
            await gateway.meter.record(usage_event(tenant, 0, True, prepared.entities > 0, 0))
            cached = f"event: verify\ndata: {json.dumps(probe.answer)}\n\n"
            return StreamingResponse(iter([cached]), media_type="text/event-stream", headers={"x-cache": "hit"})
        exchange = await gateway.upstream.answer(prepared.upstream, tenant.isolation.index, body.conversation_id)
    except (RedactionError, UpstreamError):
        await gateway.quotas.release(reservation)
        raise
    events = relay(gateway, tenant, reservation, prepared, probe, exchange)
    return StreamingResponse(events, media_type="text/event-stream", headers={"x-cache": "miss"})


async def relay(
    gateway: Gateway, tenant: Tenant, held: Reservation, prepared: Prepared, probe: Probe, exchange: Exchange
) -> AsyncIterator[str]:
    """Proxies the upstream events unchanged, then settles the quota, meters the cost and remembers the answer."""
    started = time.monotonic()
    try:
        async for chunk in exchange.events():
            yield chunk
    except UpstreamError:
        await gateway.quotas.release(held)
        return
    final, elapsed = exchange.final or {}, int((time.monotonic() - started) * 1000)
    cents = round(float(final.get("cost_eur", 0.0)) * 100)
    await gateway.quotas.settle(held, cents)
    await gateway.meter.record(usage_event(tenant, cents, False, prepared.entities > 0, elapsed))
    if final and not final.get("refusal"):
        await gateway.cache.remember(tenant.id, prepared.stored, final, probe.vector)


@routes.get("/v1/usage")
async def usage(request: Request) -> dict[str, Any]:
    gateway: Gateway = request.app.state.gateway
    tenant, month = principal(request).require_tenant(), datetime.now(UTC).strftime("%Y-%m")
    spent = await gateway.quotas.spent_cents(tenant.id, month)
    period = {"tenant": tenant.id, "plan": tenant.plan, "month": month}
    return period | {"spent_cents": spent, "monthly_cents": tenant.quota.monthly_cents}


@routes.post("/admin/tenants", status_code=202)
async def onboard(body: Onboarding, request: Request) -> dict[str, str]:
    gateway: Gateway = request.app.state.gateway
    if not principal(request).admin:
        raise AccessError(403, "the platform admin role is required")
    tenant_id = tenant_id_from(body.name)
    order = {"tenant_id": tenant_id, "requested": datetime.now(UTC).isoformat()} | body.model_dump(mode="json")
    await gateway.onboard.send_messages(ServiceBusMessage(json.dumps(order), content_type="application/json"))
    return {"tenant": tenant_id, "status": "queued"}


@routes.get("/admin/tenants")
async def tenants(request: Request) -> list[dict[str, Any]]:
    gateway: Gateway = request.app.state.gateway
    if not principal(request).admin or gateway.tenants is None:
        raise AccessError(403, "the platform admin role is required")
    fields = "c.id, c.name, c.plan, c.pii, c.isolation, c.created, c.suspended"
    query = gateway.tenants.query_items(f"SELECT {fields} FROM c")
    return [dict(item) async for item in query]


@routes.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    gateway: Gateway = request.app.state.gateway
    try:
        await gateway.quotas.redis.ping()
    except (OSError, ConnectionError) as error:
        return JSONResponse({"status": "degraded", "redis": type(error).__name__}, 503)
    return JSONResponse({"status": "ok"})


app = create_app()
