# Regulatory Intelligence as a Service

The regulatory-intelligence platform in `azure-rag-platform` works. This repository is what turns
it into a business: the same capability sold to a hundred companies, each onboarded in under two
minutes, isolated from the others in compute, data and quota, metered as an event stream, and
costed from two independent sources so that the gross margin per plan is a measured number rather
than a hope.

> Every tenant in this repository's fixtures, benchmarks and examples is an invented company. No
> customer data, name or contact exists anywhere in it.

**This repository contains no agent, by decision.** The gateway authenticates, meters, caches,
redacts and forwards; none of that is open-ended or tool-driven. The agents live upstream in
`azure-rag-platform`, which this repository treats as a service over HTTP (ADR-007, ADR-012).

## Status

Phase 7 of 9: the working agreement (`CLAUDE.md`), the budget test, typed settings, the domain
model, the whole infrastructure as code — the three workload identities and their federated
credentials, the Key Vault with the one signing key, Application Insights, the AKS cluster with its
system, spot and enterprise pools, Azure Managed Redis, Cosmos DB, Service Bus, Event Grid, the
Flex Consumption Function, the alerts and the budget, and the roles each identity holds on the
landing zone's shared services — the Kubernetes manifests: one base per tenant namespace, three
tier overlays, and the platform namespace with the Gateway, the metering drain and the KEDA-scaled
onboarding workers — and the whole gateway: tenant resolution at the edge (`app/tenancy.py`,
ADR-002), quota enforcement before work (`app/quotas.py`, ADR-003), metering as a Redis Stream
drained to Service Bus and folded into Cosmos by the Flex Consumption Function (`app/metering.py`,
`functions/`, ADR-004), the per-tenant semantic cache (`app/cache.py`, ADR-006), the typed client
for the RAG platform with its circuit breaker (`app/upstream.py`, ADR-007), the per-tenant PII
policy (`app/privacy.py`, ADR-008), telemetry with the tenant on every span, and the API surface
(`app/main.py`). The cross-tenant isolation test covers six stores, the quota test proves the
upstream is never called at the ceiling, and the metering accuracy test runs a simulated month.
Onboarding, chargeback, the console, the recording, the load-test results and the unit economics
arrive with their phases; this file is completed in phase 9.

**When the RAG platform is down**, the gateway answers 503 with `Retry-After`, releases the
tenant's quota reservation and keeps serving questions whose answers are in the tenant's cache;
new questions wait until the circuit closes (ADR-007).

Validating the manifests without a cluster:

```
make kube-validate      # kubectl kustomize on each overlay and the platform, then kubeconform -strict
```

Deploying from a workstation without Docker:

```
eval "$(azd env get-values | sed 's/^/export /')"   # the bicepparam reads the azd environment
make preview                                         # az deployment group what-if
make deploy                                          # az deployment group create, the full template
```

## Scope and simplifications

- **Size.** The specification asks for at most 50 files and 1,200 lines of Python. Its own
  mandated layout does not fit in either, so `tests/test_budget.py` enforces the counting rule in
  `CLAUDE.md`: at most 80 tracked files outside `docs/`; 1,200 lines of application Python (`app/`,
  `ops/`, `functions/`) and 900 of support Python (`tests/`, `load/`), raised once from 600 in
  phase 7 because the three thesis tests carry their own doubles; 600 lines of Bicep, raised once
  from 500 in phase 3 because the specification's own resource list did not fit even after
  compaction; 500 of YAML under `k8s/`; 700 of TypeScript; fewer than 25 comment lines. The actual
  counts are recorded here at the end of every phase.
