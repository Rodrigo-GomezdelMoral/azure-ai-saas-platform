import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from azure.core.credentials_async import AsyncTokenCredential

from app.config import Settings

FINAL_EVENT = "verify"


class UpstreamError(Exception):
    def __init__(self, retry_after: int, detail: str) -> None:
        super().__init__(detail)
        self.retry_after, self.detail = retry_after, detail


class CircuitBreaker:
    """Opens after consecutive failures, refuses for a reset window, then lets one request through to probe."""

    def __init__(self, failures: int, reset_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.failures, self.reset_s, self.clock = failures, reset_s, clock
        self.consecutive, self.open_until = 0, 0.0

    @property
    def retry_after(self) -> int:
        return max(1, int(self.open_until - self.clock()) + 1)

    def allow(self) -> bool:
        if self.clock() < self.open_until:
            return False
        if self.open_until:
            self.open_until, self.consecutive = 0.0, self.failures - 1
        return True

    def failed(self) -> None:
        self.consecutive += 1
        if self.consecutive >= self.failures:
            self.open_until = self.clock() + self.reset_s

    def succeeded(self) -> None:
        self.consecutive, self.open_until = 0, 0.0


class Exchange:
    """One upstream call: its server-sent events are yielded unchanged; the final event's data is kept for metering."""

    def __init__(self, client: httpx.AsyncClient, request: httpx.Request, breaker: CircuitBreaker) -> None:
        self.client, self.request, self.breaker = client, request, breaker
        self.final: dict[str, Any] | None = None
        self.response: httpx.Response | None = None

    async def open(self) -> "Exchange":
        try:
            self.response = await self.client.send(self.request, stream=True)
        except httpx.HTTPError as error:
            self.breaker.failed()
            raise UpstreamError(self.breaker.retry_after, f"upstream unreachable: {error}") from error
        if self.response.status_code >= 500:
            await self.response.aclose()
            self.breaker.failed()
            raise UpstreamError(self.breaker.retry_after, f"upstream answered {self.response.status_code}")
        if self.response.status_code >= 400:
            await self.response.aclose()
            raise UpstreamError(0, f"upstream refused the gateway: {self.response.status_code}")
        return self

    async def events(self) -> AsyncIterator[str]:
        if self.response is None:
            raise UpstreamError(0, "exchange not opened")
        event = ""
        try:
            async for line in self.response.aiter_lines():
                yield f"{line}\n"
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event == FINAL_EVENT:
                    self.final = json.loads(line[5:])
        except httpx.HTTPError as error:
            self.breaker.failed()
            raise UpstreamError(self.breaker.retry_after, f"upstream stream failed: {error}") from error
        finally:
            await self.response.aclose()
        self.breaker.succeeded()


class UpstreamClient:
    """A typed client for the RAG platform's /v1/answer, authenticated with the gateway's own identity."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings, credential: AsyncTokenCredential) -> None:
        self.client, self.settings, self.credential = client, settings, credential
        self.breaker = CircuitBreaker(settings.breaker_failures, settings.breaker_reset_s)

    async def answer(self, question: str, index: str, conversation_id: str) -> Exchange:
        if not self.breaker.allow():
            raise UpstreamError(self.breaker.retry_after, "upstream circuit open")
        token = await self.credential.get_token(self.settings.upstream_scope)
        body = {"question": question, "conversation_id": conversation_id, "index": index}
        headers = {"Authorization": f"Bearer {token.token}", "Accept": "text/event-stream"}
        timeout = httpx.Timeout(self.settings.upstream_timeout_s, connect=5.0)
        url = f"{self.settings.upstream_url}/v1/answer"
        request = self.client.build_request("POST", url, json=body, headers=headers, timeout=timeout)
        return await Exchange(self.client, request, self.breaker).open()
