# Cost

Every price below is the Azure retail price for Sweden Central in euros on 2026-10-21, from the Retail
Prices API, unless it is marked as measured. The measured numbers come from two independent sources that
this document reconciles: application metering, which the gateway writes per request, and Azure Cost
Management filtered by the `repo` and `tenant` tags. They never agree, and the gap is the most important
number here.

## Two sources, and the gap between them

| Source | What it measures | Where it comes from | Granularity |
|---|---|---|---|
| Application metering | the inference each answer cost, as the RAG platform reports it in the final `verify` event (`cost_eur`), in euro cents with their fractions, plus whether the answer was cached or redacted | `app/metering.py` → Service Bus → the Function → one `BillingPeriod` document per tenant and month | per request, attributed to a tenant by the token |
| Azure Cost Management | what Azure billed to the platform's resource group and its node group, by service, with the `tenant` tag where a resource carries one | `ops/chargeback.py` querying the Cost Management API by tag | per service and tag value, daily |

The gateway's metering does not see the cluster, the cache, the data plane or the messaging; Cost
Management does not see which tenant asked which question. Cost Management also bills the inference to
the RAG platform's resource group, not to this one, so the two sources measure different things by
construction. `ops/chargeback.py` therefore reports three columns per tenant: metered inference, Azure
cost that carries the tenant's tag, and the tenant's allocated share of the shared infrastructure,
allocated by its share of metered usage because usage is the only driver the platform has evidence for.
The report ends with the gap statement: what Azure billed, how much of it carries a tenant tag, and how
much is shared infrastructure no single tenant causes. The first report runs after the first month with
tenants; `chargeback.yml` produces one on the second of every month and the author commits it next to
this file.

## What the platform costs to run

Modelled from retail prices for the deployed sizes; the first chargeback report replaces these with
the billed figures.

| Resource | Size | Price | Per month at rest |
|---|---|---|---|
| AKS system node | 1 × `Standard_D4as_v5`, 0.16192 €/h | 118.20 € | 118.20 € |
| OS disk | P4 Premium SSD, 32 GB | 5.11 € | 5.11 € |
| Standard load balancer and public IP | 0.022 €/h + 0.0044 €/h | 19.27 € | 19.27 € |
| Azure Managed Redis | `Balanced_B0`, 0.01496 €/h | 10.92 € | 10.92 € |
| Log Analytics ingestion | about 0.3 GB a day of container logs and metrics with the system namespaces excluded, 2.6311 €/GB | 23.68 € | 23.68 € (modelled volume) |
| Cosmos DB serverless, Service Bus Basic, Event Grid, Flex Consumption Function | request units, operations and executions at a hundred thousand requests a month | about 1 € | about 1 € |
| AKS control plane | Free tier | 0 € | 0 € |
| **Total at rest** | | | **about 178 € a month, 6 € a day** |

Under load the spot pool adds up to two `Standard_D2as_v5` at 0.01496 €/h, about 0.72 € a day when both
run all day. The first Enterprise tenant adds a regular `Standard_D2as_v5` with its disk, 64.21 € a
month, for as long as the tenant exists. `az aks stop` deallocates the nodes and leaves the disks, the
load balancer, the public IP and Redis: about 36 € a month, 1.2 € a day. `make destroy` leaves nothing
but the soft-deleted vault, which costs nothing. The budget in `infra/modules/alerts.bicep` fires at
40 € actual and forecast, but it is scoped to the platform resource group: the nodes, their disks, the load
balancer and the public IP bill to the node resource group, which it does not see, so it does not catch a
forgotten cluster; `make stop` after every run is the guard. No Cost Management anomaly alert is deployed;
the budget's forecast threshold stands in for it, with the same blind spot.

AKS cost analysis, the per-namespace view in the portal, needs the Standard control-plane tier and an
enterprise or customer agreement; this subscription is pay-as-you-go and the tier is Free, so per-tenant
attribution of cluster cost relies on tags and on the allocation rule above.

## Unit economics per plan

The plans and their ceilings are in `app/models.py`; the prices are list prices. Inference is modelled
at 0.005 € per answer, inside the 0.0025 € to 0.0104 € per answer the RAG platform measured for its
reranker configurations (`azure-rag-platform/docs/cost.md`). The shared infrastructure, 178 € a month,
is allocated by usage share at the mix ADR-002 observes: eighty Starter, sixteen Business and four
Enterprise tenants, 23,360 € of monthly revenue.

| Plan | Price | Inference at the ceiling | Dedicated resources | Shared allocation at the ceiling | Cost at the ceiling | Gross margin at the ceiling |
|---|---|---|---|---|---|---|
| Starter | 49 € | 20.00 € (2,000 cents) | none | 0.28 € | 20.28 € | 58.6 % |
| Business | 490 € | 200.00 € (20,000 cents) | an index slot, a container: 0 € until the search service grows (ADR-010) | 2.78 € | 202.78 € | 58.6 % |
| Enterprise | 2,900 € | 2,000.00 € (200,000 cents) | one `Standard_D2as_v5` with its disk, 64.21 € | 27.81 € | 2,092.02 € | 27.9 % |

The ceiling is the worst case a plan allows, not the expectation. At thirty percent of the ceiling, which
is the usage the quotas are sized for:

| Plan | Inference | Cost | Gross margin |
|---|---|---|---|
| Starter | 6.00 € | 6.28 € | 87.2 % |
| Business | 60.00 € | 62.78 € | 87.2 % |
| Enterprise | 600.00 € | 692.02 € | 76.1 % |

Blended across the hundred tenants at thirty percent: 3,840 € of inference, 256.84 € of Enterprise nodes
and 178 € of shared infrastructure against 23,360 € of revenue, a gross margin of 81.7 %. A tenant with a
PII policy pays Azure Language per text record, 0.88 € per thousand; a Starter tenant redacting every
question at its ceiling adds 3.52 € a month and its margin at the ceiling falls to 51.4 %. The semantic
cache moves answers from the inference column to zero: every cached answer is a cent the plan keeps.

What the model is not: measured. The chargeback report is, and the first one replaces these tables.

## Where the model breaks, and at what count

| Step | When | Cost of the step |
|---|---|---|
| Search index slots | the fourteenth dedicated tenant | a second Basic service, about 65 € a month, or S1 at about 216 € (ADR-010) |
| Federated credentials | the nineteenth dedicated tenant | a second gateway identity, free (ADR-002) |
| Redis memory | about seventy thousand cached answers across tenants, at roughly 7 KB each | `Balanced_B1`, 0.02992 €/h, twice B0 |
| Enterprise pool | every Enterprise tenant | one node each, 64.21 € a month; the pool's ceiling is raised with the count |
| Cosmos serverless | sustained request-unit consumption or a tenant-month near the partition limit | provisioned autoscale on the Enterprise containers (ADR-005) |
| The system node | the Gateway proxies plus Starter and Business pods at rest on 4 vCPUs | a second system node, 118.20 € a month, or the Standard control-plane tier with zones for the Enterprise objective (`docs/operations/slo.md`) |

## Cost control

- `make stop` and `make start` around every demo and load run; `make destroy` when the evidence is in.
- Every resource carries the `repo` tag and the Enterprise pool carries its tier; the chargeback
  workflow reads both resource groups by tag.
- The budget at 40 € a month with actual and forecast alerts on the platform resource group; it does not
  see the node resource group, so it does not notice a forgotten cluster (above).
