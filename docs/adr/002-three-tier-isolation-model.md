# ADR-002 · The three-tier isolation model and what each tier shares

**Status:** accepted · **Date:** 2026-10-21

## Context

A hundred customers will share one platform. The question is not whether to isolate them but
where, because every isolation boundary has a price: a namespace is free, a node pool costs a node,
a search index consumes one of fifteen slots on the shared Basic service, a Cosmos database is
another thing to secure and bill, and an identity holds at most twenty federated credentials.
The product sells three plans, so the architecture sells three isolation levels and prices each.

## Options

1. **One shared stack with filters.** Every tenant shares the gateway deployment, the index, the
   Cosmos container and the Redis instance; isolation is a predicate on every query. Cheapest, and
   one missing predicate is a breach.
2. **Three tiers, each sharing less than the one below.** Isolation that costs nothing (keys,
   partitions, filters) is given to everyone; isolation that costs a resource (namespace, index,
   container, database, node pool) is sold with the plan.
3. **A full stack per tenant.** A cluster, a search service, a Cosmos account and a Redis per
   customer. The cleanest boundary and the one ADR-010 rejects with its cost model.

## Decision

Option 2.

| Boundary | Starter | Business | Enterprise |
|---|---|---|---|
| Kubernetes namespace | shared `tenants-starter`, one gateway deployment for all | dedicated `t-<id>`, own deployment | dedicated `t-<id>`, own deployment |
| Node pool | system node at rest, spot under load | same | dedicated `enterprise` pool, no spot, no eviction |
| Scheduling | priority 1,000, evicted first | priority 10,000 | priority 100,000, preempts lower tiers |
| Quota and limits | namespace `ResourceQuota` 1 CPU / 1 Gi, HPA 1–3 | 2 CPU / 2 Gi, HPA 2–5 | 4 CPU / 4 Gi, HPA 2–10 |
| Network | default-deny namespace policy; ingress only from the Gateway's namespace | same | same |
| Search | the shared `regulations` index | dedicated index, a copy of the corpus the tenant may extend | dedicated index |
| Cosmos | partition `/tenantId` in the shared `usage` container | dedicated container `usage-<id>` | dedicated database `t-<id>` |
| Redis | per-tenant keys (`quota:{<id>}:*`, one hash slot per tenant) and a per-tenant RediSearch index for the cache | same | same |
| Credential | a Key-Vault-signed API credential, no Entra object | an Entra app registration holding the app role `tenant:<id>` | same |
| Identity in the cluster | the shared gateway's `id-saasgw` | `id-saasgw` federated to `t-<id>/gateway` | same |
| Quota of money | 20 € a month, 20 requests a minute | 200 €, 120 a minute | 2,000 €, 600 a minute, burst |

What every tier shares, by construction and by price: the cluster and its control plane, the
Gateway and its proxies, the Managed Redis instance, the Cosmos account, the Service Bus
namespace, the Function, the search service, and the RAG platform with its model deployments.
`docs/cost.md` attributes the shared part to nobody and says so; that gap is the honest number.

The tenant is never read from a header, a path or a query string. The routing prefix `/t/<id>`
only chooses a namespace; inside it the gateway verifies that the token's tenant is the tenant the
namespace serves (`TENANT_SCOPE`), so a token for one tenant sent to another tenant's prefix is
refused with 403 (`tests/test_tenancy.py`).

### Two arithmetic limits that shape the model

- **Federated credentials.** A user-assigned identity accepts at most 20 federated credentials and
  a subject cannot be a wildcard, so each dedicated namespace's service account costs one
  credential on `id-saasgw`. Two are platform subjects (the starter gateway and the metering
  drain), which leaves 18 dedicated tenants per identity. Beyond that the onboarding takes the next
  identity of a pool (`id-saasgw-2`, …) with identical roles, declared in Bicep when needed; a
  hundred tenants at the observed Starter-to-dedicated ratio of four to one need two identities.
- **Search indexes.** The shared Basic service allows 15 indexes and the landing zone's workloads
  use two, so 13 dedicated tenants fit before the service grows to S1 (50 indexes) or a second
  service is added. ADR-010 prices both steps.

## Trade-offs

- Starter tenants share a deployment, so a Starter tenant's burst is contained by the Redis token
  bucket (ADR-003) and the namespace quota, not by a pod of its own; `docs/benchmarks/` measures
  what that is worth.
- A dedicated index per Business tenant copies the corpus (about a thousand chunks) at
  onboarding, which is seconds of work and megabytes of storage; the RAG platform answers from it
  only once it honours the `index` field the gateway sends, a change in that repository.
- Enterprise isolation is real at the node level and costs a node whenever an Enterprise tenant
  exists; the pool scales to zero when none does.
- Serverless Cosmos cannot partition throughput between tenants (ADR-005), so a dedicated
  container or database is a security and billing boundary, not a performance one.

## Revisit when

A tenant needs isolation the model does not sell: a region of its own, a dedicated Redis, or
customer-managed keys. Each of those is a fourth tier with a price, not an exception. Also when
`azure-identity` ships identity-binding support in a stable release, which removes the
twenty-credential arithmetic above.
