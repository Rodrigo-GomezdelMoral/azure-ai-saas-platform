# ADR-012 · Rejection: no agent in the gateway

**Status:** accepted · **Date:** 2026-10-21

## Context

Three repositories in this portfolio build agents with Microsoft Agent Framework. This one sells the
output of one of them to many customers. The temptation to put an agent in the gateway comes in three
forms: an agent that decides routing, an agent that reconciles costs, and an agent that assists the
operator in the console.

## Options

1. **An agent in the request path**, deciding per request whether to answer from the cache, redact,
   or route to the platform.
2. **An agent in operations**, reconciling costs or onboarding tenants by calling tools.
3. **No agent.** Every function of the gateway is deterministic and tested; the agents stay upstream.

## Decision

Option 3. The gateway authenticates, meters, caches, redacts and forwards. None of those is open-ended:
the tenant is a verified claim, the quota is arithmetic in Lua, the cache is a vector distance against a
threshold, redaction is a service call with a policy, forwarding is a proxy. An agent would add latency,
cost and non-determinism to a path whose value is being fast, cheap and predictable, and it would make
the isolation claims conditional on a model's judgement. Operations are the same: onboarding is eight
idempotent steps in a fixed order, and cost reconciliation is a query and a division.

`agent_framework` appears nowhere in this repository; `tests/test_budget.py` fails if it does. The
agents that answer the questions live in `azure-rag-platform`, consumed over HTTP (ADR-007).

## Trade-offs

- An operator assistant in the console would be convenient. If it is ever wanted, it is a separate
  service with the console as its client, never a dependency of the gateway.
- Routing decisions that depend on content (a question better served by another index) stay with the
  platform, which already runs the retrieval planner.

## Revisit when

A product decision requires per-request routing between several platforms by content. That is a new
component with its own evaluation harness, not a change to this gateway.
