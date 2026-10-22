import hashlib
import json
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from redis.asyncio import Redis
from redis.commands.search.field import TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError

from app.config import Settings

DIMENSIONS = 1536
EMBEDDING_SCOPE = "https://cognitiveservices.azure.com/.default"
Embedder = Callable[[str], Awaitable[list[float]]]


@dataclass(frozen=True)
class Probe:
    vector: list[float]
    answer: dict[str, Any] | None = None
    similarity: float = 0.0


class VectorStore(Protocol):
    async def ensure(self, tenant_id: str) -> None: ...
    async def nearest(self, tenant_id: str, vector: list[float]) -> tuple[str, float] | None: ...
    async def put(self, tenant_id: str, digest: str, question: str, answer: str, vector: list[float]) -> None: ...


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class RediSearchStore:
    """One RediSearch index per tenant over keys that start with the tenant: a query cannot see another tenant."""

    def __init__(self, redis: Redis, ttl: int = 7 * 24 * 3600) -> None:
        self.redis, self.ttl = redis, ttl

    @staticmethod
    def index(tenant_id: str) -> str:
        return f"idx:cache:{tenant_id}"

    @staticmethod
    def prefix(tenant_id: str) -> str:
        return f"cache:{tenant_id}:"

    async def ensure(self, tenant_id: str) -> None:
        vector = VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": DIMENSIONS, "DISTANCE_METRIC": "COSINE"})
        definition = IndexDefinition(prefix=[self.prefix(tenant_id)], index_type=IndexType.HASH)  # type: ignore[no-untyped-call]
        fields = [TextField("question"), TextField("answer"), vector]
        try:
            await self.redis.ft(self.index(tenant_id)).create_index(fields, definition=definition)
        except ResponseError as error:
            if "already exists" not in str(error).lower():
                raise

    async def nearest(self, tenant_id: str, vector: list[float]) -> tuple[str, float] | None:
        knn = Query("*=>[KNN 1 @embedding $vec AS distance]").return_fields("answer", "distance")
        query = knn.sort_by("distance").dialect(2)
        try:
            result = await self.redis.ft(self.index(tenant_id)).search(query, query_params={"vec": pack(vector)})
        except ResponseError:
            return None
        return (str(result.docs[0].answer), float(result.docs[0].distance)) if result.docs else None

    async def put(self, tenant_id: str, digest: str, question: str, answer: str, vector: list[float]) -> None:
        key = f"{self.prefix(tenant_id)}{digest}"
        await self.redis.hset(key, mapping={"question": question, "answer": answer, "embedding": pack(vector)})
        await self.redis.expire(key, self.ttl)


class SemanticCache:
    """Answers a question a tenant already asked; the tenant is part of the index and the key, never a filter.

    The cache is an optimisation: when the embedding service fails, the request proceeds as a miss and nothing is kept.
    """

    def __init__(self, store: VectorStore, embed: Embedder, similarity: float) -> None:
        self.store, self.embed, self.similarity = store, embed, similarity

    async def lookup(self, tenant_id: str, question: str) -> Probe:
        try:
            vector = await self.embed(question)
        except httpx.HTTPError:
            return Probe([])
        found = await self.store.nearest(tenant_id, vector)
        if found is None or 1 - found[1] < self.similarity:
            return Probe(vector)
        return Probe(vector, json.loads(found[0]), 1 - found[1])

    async def remember(self, tenant_id: str, question: str, answer: dict[str, Any], vector: list[float]) -> None:
        if not vector:
            return
        digest = hashlib.sha256(f"{tenant_id}:{question}".encode()).hexdigest()
        await self.store.put(tenant_id, digest, question, json.dumps(answer), vector)


def foundry_embedder(client: httpx.AsyncClient, settings: Settings, credential: AsyncTokenCredential) -> Embedder:
    base = settings.foundry_endpoint.rstrip("/")
    url = f"{base}/openai/deployments/{settings.embedding_deployment}/embeddings?api-version=2024-10-21"

    async def embed(text: str) -> list[float]:
        token = await credential.get_token(EMBEDDING_SCOPE)
        response = await client.post(url, json={"input": text}, headers={"Authorization": f"Bearer {token.token}"})
        response.raise_for_status()
        return [float(value) for value in response.json()["data"][0]["embedding"]]

    return embed
