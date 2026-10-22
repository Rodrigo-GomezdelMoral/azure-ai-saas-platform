# Service level objectives

Objectives are stated per tier because the tiers are priced for different continuity, not because the
code differs. They are measured at the Gateway, on requests that carried a valid tenant; a 429 is the
contract working, not an error, and is excluded from availability but reported as the quota rejection
rate (a business signal with its own alert).

## Objectives

| Tier | Availability, monthly | Added latency, p95 on a cache miss | Added latency, p95 on a cache hit | Onboarding |
|---|---|---|---|---|
| Starter | 99.0 % (7.2 h of error budget) | ≤ 300 ms | ≤ 150 ms | ≤ 2 min |
| Business | 99.5 % (3.6 h) | ≤ 300 ms | ≤ 150 ms | ≤ 2 min |
| Enterprise | 99.9 % (43 min) | ≤ 300 ms | ≤ 150 ms | ≤ 2 min |

*Added latency* is the gateway's own contribution: from the request's arrival to the first byte of the
upstream's stream, minus the upstream's time to first byte, so that the RAG platform's latency is its own
objective and not this one. The baseline benchmark measures it against the in-cluster stub, which answers
in microseconds, so its p95 is the gateway's p95 (`docs/benchmarks/`). The numbers above are objectives;
the measured values are published with the benchmarks and the objectives are revised from them.

**What this estate can and cannot promise.** The cluster runs the Free control-plane tier, which carries
no financially backed uptime SLA, and one system node at rest. The Starter and Business objectives are
achievable on it; the Enterprise objective is the production posture, which is the Standard tier with
availability zones, three system nodes and the Enterprise pool spread across zones. The document says so
rather than claiming 99.9 % from one node.

## Error budget policy

The budget is the complement of the objective over a calendar month, consumed by failed or slow
requests on the tenant's tier.

- **Below 50 % consumed:** normal operation; deployments through `deploy.yml` at will.
- **50 % to 100 %:** no deployment except fixes; the deploy workflow's rollback is the first response
  to any regression; the on-call reviews the alerts' history (`infra/modules/alerts.bicep`: gateway 5xx,
  quota rejections, dead letters, node CPU and memory pressure).
- **Spent:** the tier is treated as in incident until the month ends or the cause is fixed; Starter
  pods are the first to yield under node pressure by priority class, which is the designed order of
  degradation, and an Enterprise budget spent is a post-incident review with the customer.

## Degradation modes, in the order they are designed to happen

1. **The RAG platform is down or slow.** The circuit breaker opens after five consecutive failures and
   refuses for thirty seconds; the gateway answers 503 with `Retry-After`, releases the reservation,
   and keeps serving cache hits (ADR-007). Availability counts those 503s against the budget; cache
   hits keep the budget alive.
2. **Managed Redis is unreachable.** The gateway fails closed: no reservation, no answer, 503 from the
   health probe. Quotas are the contract; serving without them would be the breach.
3. **Node pressure.** Starter pods are evicted first, then Business; Enterprise preempts. Spot evictions
   land on the Starter and Business budgets (ADR-009).
4. **The embedding service fails.** The cache behaves as a miss and nothing is remembered; latency rises
   to the upstream's, availability is unaffected.

## Disaster recovery

| Component | State | Loss on a regional outage | Recovery |
|---|---|---|---|
| Cluster, Gateway, tenant namespaces | declarative: `infra/`, `k8s/`, the tenant documents | none; everything is rebuilt | `make deploy` in the paired region, `deploy.yml`, then `python -m ops.onboard` per tenant from the tenant documents (idempotent) |
| Cosmos DB (tenants, credentials, usage) | the system of record | up to the backup interval | periodic backup, 4-hour interval, restored to a new account; the production posture is continuous backup with point-in-time restore |
| Key Vault (signing key) | one key | none: soft delete and purge protection; a key backup (`az keyvault key backup`) restores within the geography | restore the key, re-publish the JWKS document, or issue new credentials |
| Managed Redis (quotas, stream, cache) | derived and ephemeral by design | the current month's spend counters, pending usage events not yet drained, cached answers | rebuilt by use; the drain's `XAUTOCLAIM` replays what a dead consumer left; a lost stream costs the undrained events, bounded by the drain interval |
| Service Bus queues | in flight | messages not yet consumed | the queues are recreated by the template; onboarding orders are re-submitted from the console |
| Container image | built from `main` | none | `deploy.yml` rebuilds and pushes |

**RTO: 4 hours.** The template deploys in about 40 minutes (the cluster is the long pole), the image
builds in 10, the platform and starter namespaces apply in 5, and re-onboarding a hundred tenants from
their documents at under two minutes each, ten at a time, fits in the remainder. **RPO: 4 hours** for
billing records, the periodic backup interval of the Cosmos account; metering in flight is lost for at
most the drain interval, and the design accepts that a regional disaster may under-bill the hour in which
it happened. The production posture of continuous backup brings the RPO to minutes.

## Runbook: regional outage

1. Confirm the region, not the cluster, is down: `az aks show` fails or the Gateway's address does not
   answer `/healthz` for every namespace. A single namespace failing is a rollout problem: `make rollback`.
2. `az deployment group create` with the template in the paired region's resource group, with
   `VAULT_CREATE_MODE=recover` if the vault name is reused and the region allows it; otherwise a new vault
   and a new signing key, and every credential is reissued.
3. Restore the Cosmos account from its periodic backup into the new region through a support request,
   or create the account and re-run onboarding for every tenant: the tenant documents are the input, the
   credentials are reissued.
4. Run `deploy.yml` against the new cluster; it renders the wrappers from the new deployment's outputs.
5. Repoint customers: the Gateway's public IP changes; the DNS record customers use is the one thing this
   repository does not own, and ADR-011 names the day Front Door takes that over.
