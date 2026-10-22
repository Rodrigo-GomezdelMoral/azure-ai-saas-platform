# ADR-006 · A semantic cache per tenant, and the breach it prevents

**Status:** accepted · **Date:** 2026-10-21

## Context

Regulatory questions repeat: the same company asks what Article 10 requires many times, phrased
slightly differently each time. Answering from a cache saves the upstream platform's cost (ADR-003
meters it in cents) and seconds of latency. A semantic cache keyed on embeddings does that for
paraphrases, and it also creates the most dangerous bug in a multi-tenant product: a cache hit
that serves one customer an answer built for another. The answer is not only text; it carries
citations and, for a tenant with a dedicated index, may reflect documents only that tenant can see.

## Options

1. **One shared cache with a tenant field**, filtered at query time. One missing filter is a
   breach, and vector search makes the filter a second-class clause.
2. **One cache namespace per tenant by construction**: a RediSearch index per tenant whose
   definition only covers keys that start with the tenant's prefix, so a query is scoped before it
   runs and there is no filter to forget.
3. **No cache.** Correct and twice as expensive for the questions customers actually ask.

## Decision

Option 2. `app/cache.py` creates `idx:cache:<tenant>` over the prefix `cache:<tenant>:` at
onboarding, stores an answer as a hash under `cache:<tenant>:<sha256 of tenant and question>` with a
seven-day TTL, and looks up with a KNN query against that tenant's index only. The embedding comes
from the shared Foundry account's `text-embedding-3-small` deployment with the gateway's identity.
A hit requires cosine similarity at or above the plan-independent threshold `CACHE_SIMILARITY`
(0.92); a miss returns the vector so the answer, once produced, is remembered without a second
embedding call.

The cross-tenant case is proved, not asserted: `tests/test_isolation.py` stores an answer for one
tenant and shows that the same question from another tenant misses, and that no key outside that
tenant's prefix exists. The RediSearch dialect, field types and the `FT.CREATE` and `FT.SEARCH`
shapes are the ones `redis-py` 8 exposes through `redis.ft(index).create_index` and `.search`;
the unit tests run the key and index construction on `fakeredis` and the search semantics on an
in-memory vector store, because `fakeredis` has no RediSearch, and the live check of the KNN query
is on the end-of-repository list.

Policy interacts with privacy (ADR-008): a tenant whose PII policy forbids storage never writes to
the cache, so the cache never holds what the tenant did not allow the platform to keep.

## Trade-offs

- One index per tenant costs memory and a creation step at onboarding; Redis Enterprise handles
  hundreds of small indexes, and the Balanced B0 instance has room for the hit rates this product
  will see before it needs a bigger SKU.
- A cached answer can be stale when the regulation corpus changes. The TTL bounds staleness to a
  week; a corpus update that must propagate faster flushes the tenant's prefix.
- The similarity threshold is a product decision. Too low returns a neighbour's answer to a
  different question; `docs/benchmarks/` reports the hit rate the load tests observed.

## Revisit when

Answers become personalised beyond the tenant (per user, per conversation), in which case the
prefix grows a second component; or when the cache is worth sharing across tenants for the public
corpus, which would need a provably tenant-free answer and a separate, deliberately shared index.
