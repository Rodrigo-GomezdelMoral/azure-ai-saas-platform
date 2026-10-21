from collections.abc import Awaitable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from app.models import PiiPolicy, Tenant


class RedactionError(Exception):
    """Azure Language could not redact: a tenant whose policy requires redaction gets no answer rather than a leak."""


@dataclass(frozen=True)
class Prepared:
    upstream: str
    stored: str
    entities: int = 0


class PiiDetector(Protocol):
    def recognize_pii_entities(self, documents: list[str], **kwargs: Any) -> Awaitable[Any]: ...


class Redactor:
    """Azure Language on the shared Foundry account within its limits: 5,120 characters per document, 5 per call."""

    def __init__(self, client: PiiDetector, characters: int = 5120, documents: int = 5, language: str = "en") -> None:
        self.client, self.characters, self.documents, self.language = client, characters, documents, language

    def chunks(self, text: str) -> Iterator[str]:
        limit = self.characters
        while text:
            cut = len(text) if len(text) <= limit else max(text.rfind(" ", 0, limit), limit // 2)
            yield text[:cut]
            text = text[cut:]

    async def redact(self, text: str) -> tuple[str, int]:
        pieces, entities = list(self.chunks(text)), 0
        redacted: list[str] = []
        for start in range(0, len(pieces), self.documents):
            batch = pieces[start : start + self.documents]
            for result in await self.client.recognize_pii_entities(batch, language=self.language):
                if result.is_error:
                    raise RedactionError(str(result.error.message))
                redacted.append(str(result.redacted_text))
                entities += len(result.entities)
        return "".join(redacted), entities


class Privacy:
    """The PII policy is the tenant's configuration: what leaves for the upstream and what the platform may keep."""

    def __init__(self, redactor: Redactor) -> None:
        self.redactor = redactor

    async def prepare(self, tenant: Tenant, text: str) -> Prepared:
        if tenant.pii is PiiPolicy.NONE:
            return Prepared(text, text)
        redacted, entities = await self.redactor.redact(text)
        if tenant.pii is PiiPolicy.BEFORE_UPSTREAM:
            return Prepared(redacted, redacted, entities)
        return Prepared(text, redacted, entities)
