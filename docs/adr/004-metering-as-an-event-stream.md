# ADR-004 · Metering is an event stream, not a database write per request

**Status:** accepted · **Date:** 2026-10-21

## Context

Every request has to be billed to a tenant, and the two obvious designs are both wrong for a
hundred customers. Writing a billing row per request puts a database on the hot path: the
answer waits for Cosmos, a Cosmos outage stops the product, and the bill is as consistent as the
write path's error handling. Counting in memory and flushing later loses the count when a pod is
evicted, which on a spot pool is routine.

## Options

1. **A Cosmos write per request** from the gateway, synchronous. Simple, slow, coupled.
2. **In-memory counters** flushed on a timer. Fast, and the loss on eviction is unbounded.
3. **A Redis Stream** written by the gateway, drained by a consumer group to Service Bus, folded by
   an Azure Function into one document per tenant and month in Cosmos.
4. **Event Hubs** instead of Service Bus. Higher throughput than this product will see for years
   and a per-hour base price regardless of traffic.

## Decision

Option 3. The request path does one `XADD` to the stream `usage` (`app/metering.py`, `Meter`),
bounded at a hundred thousand entries so Redis memory cannot grow without limit. A single
consumer-group reader, `meter-drain` in the `platform` namespace, reads batches with `XREADGROUP`,
forwards them to the Service Bus queue `usage` as one JSON message per event, acknowledges with
`XACK`, and reclaims with `XAUTOCLAIM` whatever a reader that died left pending for more than a
minute. The Function `functions/aggregate.py` runs on Flex Consumption with a Service Bus trigger in
batches of a hundred, folds each batch into per-tenant, per-month increments and applies them with
Cosmos **patch** operations on the `BillingPeriod` document, which keeps the increments atomic and
lets several Function instances write the same month concurrently. Each event names its billing
store (database and container) because the tier decides where a tenant's usage lives (ADR-002).

Delivery is at-least-once at every hop, so the design is honest about duplicates rather than
pretending they cannot happen: a reader that forwards a batch and dies before acknowledging it
forwards it again. `tests/test_metering.py` simulates a month of three tenants, proves the bill
equals the consumption exactly without redelivery, and bounds the overcount of one redelivered
batch to that batch, under two percent of a month of that size. Settlement of the quota (ADR-003) uses the same measured
cost, so the amount a tenant was charged against its plan and the amount on its bill come from
one number.

The Function scales on queue length by itself (the Flex Consumption scale controller watches the
queue); KEDA in the cluster scales the onboarding workers, not this path.

## Trade-offs

- The bill lags consumption by the drain interval plus the Function's batch, seconds to a minute,
  which is fine for a monthly statement and would not be for a per-request balance.
- Two queues of state, the stream and Service Bus, each with its own dead-letter semantics: the
  stream's pending list and the queue's dead-letter sub-queue, the latter alerted on.
- Duplicates are bounded, not eliminated. An idempotent fold would need a per-event record, which
  is the database write per request this decision avoids; the tolerance is stated in the test and in
  `docs/cost.md`.

## Revisit when

A product promise needs exactly-once billing (prepaid balances, hard cut-offs mid-request), when
the per-event record becomes worth its cost; or when traffic exceeds what one drain reader moves,
which is the moment to add consumers to the group, not to change the design.
