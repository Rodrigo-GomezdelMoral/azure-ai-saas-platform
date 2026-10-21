# ADR-010 · Rejection: no database per tenant, with the cost model that decides it

**Status:** accepted · **Date:** 2026-10-21

## Context

A database per tenant is the cleanest data boundary there is: nothing to filter, nothing to partition,
a backup and a key per customer. Every reviewer asks for it, and the honest answer is a cost model, not a
preference. Prices are the Azure retail prices for Sweden Central on 2026-10-21 in euros; the tenant mix
is the one ADR-002 observes, eighty Starter, sixteen Business and four Enterprise tenants, 23,360 € of
monthly revenue at list price.

## Options

1. **A Cosmos account, a search service and a Redis per tenant.** The full boundary. The platform's
   shared services cost about 178 € a month at rest (`docs/cost.md`); a hundred of them is 17,800 €, 76 %
   of revenue before a single answer is paid for. There is no business at any tier.
2. **One account, a database per tenant with provisioned throughput.** The smallest throughput a
   database can own is 400 RU/s, 0.007 € per 100 RU/s per hour, so 20.4 € a month per tenant. Eighty
   Starter tenants would pay 1,632 € a month for throughput they never use, 42 % of the Starter tier's
   revenue; the Starter margin at the plan's ceiling falls from 59 % to 17 %, and at typical usage the
   throughput costs more than the inference it isolates.
3. **One serverless account, boundaries sold by tier.** A partition for Starter, a container for
   Business, a database for Enterprise, all in one account with one set of roles, one backup policy and
   one connection per workload. Serverless makes a database or a container free to create; the price of
   each tier's boundary is the operations of creating and securing it, which `ops/onboard.py` automates.

## Decision

Option 3 (ADR-002, ADR-005). What a database per tenant would buy, and why it is not bought at this
scale:

| It buys | Who needs it | The answer in this model |
|---|---|---|
| a throughput boundary | tenants whose bursts would starve others | there is none to buy on serverless; the quota gate isolates before the request unit is spent, and the production posture (ADR-005) is provisioned autoscale on the Enterprise containers only |
| a blast radius per tenant | a compromised credential or a bad migration | the tenant is a verified claim on every read and write, and the isolation test covers every store; a dedicated database (Enterprise) and container (Business) narrow the radius where the plan pays for it |
| backup and restore per tenant | a customer who deletes its own data | one account, one backup, a restore of one tenant's documents by partition key: an operation, not a resource |
| customer-managed keys | regulated customers | a fourth tier with a dedicated account, priced at the 20.4 € of throughput plus the operations, when a contract asks for it |

**The search service is the real ceiling, not the database.** Dedicated indexes are one of fifteen slots
on the shared Basic service, and the landing zone uses two: the fourteenth dedicated tenant needs either
a second Basic service (0.0889 € an hour, about 65 € a month, thirteen more slots) or the S1 tier
(0.2957 € an hour, about 216 € a month, fifty slots and larger partitions). The second Basic service is
the cheaper step and the one the onboarding takes by pointing new tenants at it; S1 is the step when the
corpus outgrows Basic's partition size. Spread over the dedicated tenants it serves, either step costs a
Business tenant under 12 € a month, a tenth of its margin at the ceiling.

## Trade-offs

- Starter tenants share a container; a bug in the partition key handling would be a cross-tenant leak.
  That is why the isolation test is parameterised over every store and runs on every pull request.
- A single account is a single region and a single backup policy; Enterprise customers who need more
  are the fourth tier above, not an exception.
- Serverless bills request units at a premium under sustained load; ADR-005 names the posture.

## Revisit when

A tenant's contract requires a dedicated account, customer-managed keys or a region of its own; or the
measured request-unit bill of the shared container exceeds what provisioned throughput would cost for
the Starter tier as a whole, about 20 € a month, at which point the shared container moves to provisioned
autoscale and nothing else changes.
