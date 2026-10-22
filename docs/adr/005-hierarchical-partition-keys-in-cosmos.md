# ADR-005 · Hierarchical partition keys in Cosmos DB, and what serverless cannot show

**Status:** accepted · **Date:** 2026-10-21

## Context

Usage is folded into one document per tenant and month. A hundred tenants over years is a wide,
skewed data set: a few Enterprise tenants write most of the records. The partition design decides
whether a tenant's month can grow without bound, whether a query for one tenant touches other tenants'
partitions, and whether throughput can ever be partitioned between customers.

## Options

1. **Partition key `/tenantId` only.** Every query for a tenant is single-partition, but a tenant's
   whole history shares one logical partition, which has a 20 GB ceiling and one throughput share.
2. **Synthetic key `tenantId_month`.** Avoids the ceiling, but a query across a tenant's months fans
   out to every partition unless the application knows the months.
3. **Hierarchical partition keys `/tenantId` then `/month`.** Queries that give the tenant stay inside
   that tenant's partitions; queries that give both are single-partition; no logical partition exceeds
   one tenant-month.

## Decision

Option 3, on every usage container the platform creates (`infra/modules/data.bicep` for the shared
container, `ops/onboard.py` for the dedicated containers and databases), `kind: MultiHash`, version 2.
The Function patches the period document with atomic increments (`functions/aggregate.py`), so the
write path is one point write per batch and tenant-month.

**What serverless cannot demonstrate.** The account is serverless: it bills request units as consumed and
has no provisioned throughput to divide between tenants. A Business tenant's dedicated container and an
Enterprise tenant's dedicated database are therefore security and billing boundaries, not performance
boundaries; a burst by one tenant and a burst by another draw on the same regional serverless capacity.
The noisy-neighbour proof in `docs/benchmarks/` is a gateway proof, enforced before any request unit is
spent (ADR-003), and this document says so rather than implying a database guarantee that does not exist.

**Production posture.** Provisioned throughput with autoscale, dedicated to Enterprise containers, and a
shared autoscale pool for the Starter container, so that the throughput a customer pays for is the
throughput the customer gets. Serverless is the correct choice for this estate, which runs intermittently,
and the correct starting point for a product whose usage is unknown.

## Trade-offs

- Hierarchical keys require the full key for point reads; the Function and the chargeback report always
  have it. A cross-tenant report over a month fans out across tenants, which is what a report should do
  once a month and never on the request path.
- Serverless caps a container at a regional throughput ceiling and bills a premium per request unit at
  sustained load; the posture above is the answer when load becomes sustained.

## Revisit when

A tenant's monthly usage document or request-unit consumption approaches the serverless limits, or a
contract requires throughput isolation. The posture is a change of account settings and a migration,
not a change of model.
