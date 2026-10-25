# ADR-007 · The RAG platform is consumed over HTTP against its contract, never imported

**Status:** accepted · **Date:** 2026-10-21

## Context

`azure-rag-platform` already answers regulatory questions with verified, cited claims. This
repository sells that capability to many customers. The temptation is to import its retrieval and
generation code, or to copy it, and run it inside the gateway: one process, no network hop, no
second deployment. That would also make this repository a second owner of a pipeline it did not
design, and the evaluation harness that gates the platform's releases would no longer gate what
customers receive.

## Options

1. **Import the platform's packages** into the gateway and call `answer()` in-process.
2. **Copy the pipeline** into this repository and evolve it separately.
3. **Consume the platform over HTTP** as an external dependency with a contract, a typed client, a
   circuit breaker and a documented degradation mode.

## Decision

Option 3. The contract is the platform's OpenAPI document, exported offline with `app.openapi()`
and kept as `docs/upstream-openapi.json`: `POST /v1/answer` takes `{question, conversation_id}` and
answers with server-sent events named `plan`, `search`, `rerank`, `answer` and finally `verify`,
each a `PhaseEvent` envelope (`phase`, `trace_id`, `elapsed_ms`, `data`); the `data` of the last one
is the verified `Answer` with `cost_eur` and `timings_ms`. OpenAPI does not describe the
event stream, so `app/upstream.py` is a hand-written typed client against that contract rather
than generated code, and `tests/test_gateway.py` holds the recorded shape of the stream.

The client authenticates with the gateway's own identity: `id-saasgw` holds the `Rag.Query` app
role on the platform's API registration, and the token comes from the workload identity, so no
shared secret exists between the two systems. The gateway sends the tenant's `index` with every
question; the platform ignores the field until it is taught to honour it, which is a small change
in that repository and the author's pull request, and `docs/security.md` says so.

The stream is proxied unchanged, line by line, and only the `verify` event's data is parsed, for
`cost_eur`. The gateway never interprets intermediate phases: they are the platform's progress
report to the customer, not the gateway's business.

**Degradation.** A `CircuitBreaker` opens after five consecutive failures (connection errors,
timeouts, or a 5xx) and refuses for thirty seconds before letting one request probe. While it is
open, or when a request fails, the gateway answers **503 with `Retry-After`** in `problem+json` and
releases the tenant's quota reservation; a question whose answer is in the tenant's semantic cache
is still served, because the cache is consulted before the upstream is called. That is the whole
degradation mode, and it is what the README states: cached answers keep flowing, new questions
wait. A 4xx from the platform is not an outage but a configuration fault (the gateway's role, the
request shape) and is reported as 503 without tripping the breaker.

## Trade-offs

- A network hop and a second token exchange per request, measured in `docs/benchmarks/` as part
  of the gateway's own latency.
- The platform's rate limits on its model deployments are shared by every tenant; the gateway's
  quotas (ADR-003) are the only thing that keeps one tenant from exhausting them for all, which
  is why the load tests use an in-cluster stub rather than the real platform.
- Contract drift is detected by tests against a recorded stream, not at compile time. The platform
  is versioned and its own CI gates its contract.

## Revisit when

The platform exposes token counts or a per-tenant index in its final event or request, at which
point this client follows; or the two repositories merge under one team, when an in-process call
becomes an option again.
