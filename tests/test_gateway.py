import asyncio
import json
from typing import Any

import httpx
import pytest
from azure.ai.textanalytics.aio import TextAnalyticsClient
from azure.core.credentials import AccessToken

from app.cache import SemanticCache
from app.config import Settings
from app.main import Gateway, create_app
from app.metering import Meter
from app.models import PiiPolicy
from app.privacy import Privacy, Redactor
from app.quotas import QuotaGate
from app.tenancy import TenantResolver
from app.upstream import UpstreamClient
from tests.conftest import Keys, MemoryStore
from tests.test_metering import MemoryVectorStore, fake_embedder

CLAIM = {"text": "Article 10 requires data governance.", "chunk_ids": ["c1"], "citation_label": "AI Act art. 10"}
COST_EUR = 0.0142
ANSWER: dict[str, Any] = {"trace_id": "t1", "claims": [CLAIM], "citations": [], "refusal": None, "cost_eur": COST_EUR}
FINAL: dict[str, Any] = {"phase": "verify", "trace_id": "t1", "elapsed_ms": 9, "data": ANSWER}
STREAM = f'event: plan\ndata: {{"phase": "plan", "data": {{}}}}\n\nevent: verify\ndata: {json.dumps(FINAL)}\n\n'


class Upstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, text="down")
        return httpx.Response(200, content=STREAM.encode(), headers={"content-type": "text/event-stream"})


class PiiResult:
    def __init__(self, document: str) -> None:
        self.redacted_text, self.is_error = document.replace("Alice", "[PERSON]"), False
        self.entities = [1] if "Alice" in document else []


class FakePii:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def recognize_pii_entities(self, documents: list[str], **kwargs: Any) -> list[Any]:
        self.batches.append(list(documents))
        return [PiiResult(document) for document in documents]


class FakeCredential:
    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("token", 0)


class Sender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_messages(self, message: Any) -> None:
        self.sent.append(json.loads(b"".join(message.body).decode()))
        assert await asyncio.sleep(0.01, self.sent[-1]) is self.sent[-1], "sends overlapped on one Service Bus sender"


class Ticker:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class World:
    def __init__(self, settings: Settings, store: MemoryStore, keys: Keys, redis: Any) -> None:
        self.upstream, self.pii, self.sender, self.clock = Upstream(), FakePii(), Sender(), Ticker()
        self.keys, self.store, self.redis, meter = keys, store, redis, Meter(redis, "usage")
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.upstream.handler))
        self.resolver = TenantResolver(settings, store, keys)
        cache = SemanticCache(MemoryVectorStore(), fake_embedder(), settings.cache_similarity)
        upstream = UpstreamClient(http, settings, FakeCredential())  # type: ignore[arg-type]
        upstream.breaker.failures, upstream.breaker.clock = 3, self.clock
        privacy = Privacy(Redactor(self.pii, settings.pii_document_characters, settings.pii_documents_per_request))
        self.gateway = Gateway(
            settings, self.resolver, QuotaGate(redis), cache, privacy, upstream, meter, None, self.sender
        )
        transport = httpx.ASGITransport(app=create_app(self.gateway))
        self.client = httpx.AsyncClient(transport=transport, base_url="http://gateway")

    def headers(self, *roles: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys.entra_token(list(roles))}"}

    async def ask(self, tenant: str, question: str = "What does Article 10 require?") -> httpx.Response:
        headers = self.headers(f"tenant:{tenant}")
        return await self.client.post("/v1/answer", json={"question": question}, headers=headers)


@pytest.fixture
async def world(settings: Settings, store: MemoryStore, keys: Keys, redis: Any) -> World:
    built = World(settings, store, keys, redis)
    await built.resolver.refresh_keys()
    return built


async def test_answer_streams_upstream_events_unchanged_and_meters_the_final_event(world: World) -> None:
    response = await world.ask("acme")
    assert response.status_code == 200 and response.headers["x-cache"] == "miss" and response.text == STREAM
    sent = world.upstream.requests[0]
    assert sent["index"] == "regulations" and sent["question"].startswith("What")
    _, fields = (await world.redis.xrange("usage"))[0]
    assert fields["tenant_id"] == "acme" and fields["cached"] == "0"
    assert float(fields["cents"]) == COST_EUR * 100
    usage = await world.client.get("/v1/usage", headers=world.headers("tenant:acme"))
    assert usage.json()["spent_cents"] == pytest.approx(COST_EUR * 100) and usage.json()["monthly_cents"] == 2_000


async def test_tenant_at_its_ceiling_is_rejected_before_any_upstream_call(world: World) -> None:
    """The quota thesis: the proof is that the upstream transport never saw a request, not the status code."""
    quota, gate = world.store.tenants["acme"].quota, world.gateway.quotas
    while (await gate.reserve("acme", quota))[0].allowed:
        pass
    response = await world.ask("acme")
    assert response.status_code == 429 and response.headers["content-type"].startswith("application/problem+json")
    assert "quota exhausted" in response.json()["detail"] and int(response.headers["retry-after"]) >= 1
    assert world.upstream.requests == [] and await world.redis.xlen("usage") == 0


async def test_second_identical_question_is_answered_from_the_cache(world: World) -> None:
    await world.ask("acme")
    hit = await world.ask("acme", "what does article 10 require?")
    assert hit.status_code == 200 and hit.headers["x-cache"] == "hit"
    assert json.loads(hit.text.split("data: ")[1]) == FINAL
    assert len(world.upstream.requests) == 1
    entries = await world.redis.xrange("usage")
    assert [fields["cached"] for _, fields in entries] == ["0", "1"] and float(entries[1][1]["cents"]) == 0


async def test_pii_policy_decides_what_leaves_and_what_is_kept(world: World) -> None:
    globex = world.store.tenants["globex"]
    world.store.tenants["globex"] = globex.model_copy(update={"pii": PiiPolicy.BEFORE_UPSTREAM})
    await world.ask("globex", "Alice asked what Article 10 requires")
    assert world.upstream.requests[-1]["question"] == "[PERSON] asked what Article 10 requires"
    world.store.tenants["globex"] = globex.model_copy(update={"pii": PiiPolicy.BEFORE_STORAGE})
    await world.ask("globex", "Alice asked what Article 11 requires")
    assert world.upstream.requests[-1]["question"] == "Alice asked what Article 11 requires"
    vectors = [vector for _, vector in world.gateway.cache.store.entries["globex"]]  # type: ignore[attr-defined]
    assert await fake_embedder()("[PERSON] asked what Article 11 requires") in vectors
    assert (await world.redis.xrange("usage"))[-1][1]["redacted"] == "1"


async def test_redaction_respects_the_service_limits() -> None:
    pii = FakePii()
    assert TextAnalyticsClient("https://foundry.invalid", FakeCredential())  # type: ignore[arg-type]
    text = " ".join(["Alice"] * 5_000)
    redacted, entities = await Redactor(pii, 5120, 5).redact(text)
    assert [len(batch) for batch in pii.batches] == [5, 1]
    assert all(len(document) <= 5120 for batch in pii.batches for document in batch)
    assert redacted.count("[PERSON]") == 5_000 and entities == 6 and len(redacted) > len(text)


async def test_circuit_breaker_opens_after_failures_and_probes_after_the_reset(world: World) -> None:
    world.upstream.status = 503
    assert [(await world.ask("acme")).status_code for _ in range(3)] == [503, 503, 503]
    opened = await world.ask("acme")
    assert opened.status_code == 503 and "circuit open" in opened.json()["detail"]
    assert int(opened.headers["retry-after"]) >= 1
    assert len(world.upstream.requests) == 3
    world.clock.now += 31
    world.upstream.status = 200
    assert (await world.ask("acme")).status_code == 200 and len(world.upstream.requests) == 4


async def test_admin_role_queues_a_burst_of_onboardings_and_tenants_cannot(world: World) -> None:
    order = {"name": "Acme Robotics", "plan": "business", "pii": "before_storage"}
    refused = await world.client.post("/admin/tenants", json=order, headers=world.headers("tenant:acme"))
    burst = [world.client.post("/admin/tenants", json=order, headers=world.headers("Platform.Admin")) for _ in range(9)]
    queued = await asyncio.gather(*burst)
    assert refused.status_code == 403 and {r.status_code for r in queued} == {202} and len(world.sender.sent) == 9
    assert queued[0].json() == {"tenant": "acme-robotics", "status": "queued"}
    assert world.sender.sent[0]["plan"] == "business" and world.sender.sent[0]["tenant_id"] == "acme-robotics"


async def test_unauthenticated_requests_touch_nothing(world: World) -> None:
    response = await world.client.post("/v1/answer", json={"question": "What does Article 10 require?"})
    assert response.status_code == 401 and world.upstream.requests == [] and await world.redis.xlen("usage") == 0
    assert (await world.client.get("/healthz")).json() == {"status": "ok"}
