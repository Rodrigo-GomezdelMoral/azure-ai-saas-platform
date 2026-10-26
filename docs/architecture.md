# Architecture

Regulatory Intelligence as a Service is a multi-tenant gateway in front of the RAG platform in
`azure-rag-platform`, consumed over HTTP against its OpenAPI contract and never imported (ADR-007). The
gateway owns everything a hundred paying customers add to a platform that already works: tenant
resolution, quotas, metering, a per-tenant semantic cache, a per-tenant PII policy, onboarding and cost
attribution. This document holds the container diagram, the network diagram, the tenant isolation
diagram per tier, the request path and the identities.

## Container diagram

```mermaid
graph TB
    C["Customer applications<br/>Entra token or platform credential"] --> GW["Gateway API · approuting-istio<br/>namespace ingress, one public IP"]
    ADM["Admin console and admin API<br/>Entra sign-in, by port-forward only"] -.-> S
    GW -->|"/v1 → tenants-starter"| S["Starter gateway deployment<br/>shared namespace, TENANT_SCOPE=starter"]
    GW -->|"/t/{id} → t-{id}"| D["Dedicated gateway deployments<br/>Business and Enterprise namespaces"]
    S --> R["Azure Managed Redis<br/>quota buckets · usage stream · one cache index per tenant"]
    D --> R
    S --> F["Shared Foundry account<br/>Language PII detection · embeddings"]
    D --> F
    S --> RAG["azure-rag-platform /v1/answer<br/>server-sent events, cost_eur in the final event"]
    D --> RAG
    R --> MD["Metering drain<br/>consumer group → Service Bus"]
    MD --> SB[["Service Bus<br/>queues usage and onboarding"]]
    SB --> FN["Flex Consumption Function<br/>folds usage into one BillingPeriod per tenant-month"]
    FN --> COS[("Cosmos DB serverless<br/>partition /tenantId then /month")]
    S --> COS
    D --> COS
    S -->|"POST /admin/tenants → order"| SB
    SB --> ONB["Onboarding workers<br/>KEDA scales on queue length"]
    ONB --> K8S["Kubernetes API<br/>namespace, route and quota per tenant"]
    ONB --> GRAPH["Microsoft Graph<br/>app registration and tenant app role"]
    ONB --> SRCH["Azure AI Search<br/>dedicated index per Business and Enterprise tenant"]
    ONB --> KV["Key Vault<br/>credential-signing key, sign only"]
    ONB --> EG["Event Grid topic<br/>saas.tenant.onboarded"]
    COS --> CB["Chargeback<br/>metering against Cost Management by tag"]
```

The gateway image is one container (`Dockerfile`) that runs as the tenant gateways, the metering drain
and the onboarding workers, selected by the deployment's arguments. The Function is packaged on its own
(`functions/`) and shares no code with the gateway.

## Network diagram

```mermaid
graph LR
    subgraph internet["Internet"]
        CL["Customers and the operator"]
    end
    subgraph vnet["AKS managed virtual network · Azure CNI overlay · Cilium data plane and policy"]
        LB["Standard load balancer<br/>one public IP, HTTP listener on port 80"] --> GWP["Gateway proxies<br/>namespace ingress"]
        GWP --> NS1["tenants-starter<br/>default deny · ingress from namespace ingress only"]
        GWP --> NS2["t-{id} per dedicated tenant<br/>default deny · ingress from namespace ingress only"]
        NS1 -. "egress: DNS, TCP 443, 10000 and 5671 to public ranges only" .-> OUT["Outbound through the load balancer"]
        NS2 -. "same policy" .-> OUT
    end
    CL --> LB
    OUT --> PAAS["Entra · Cosmos DB · Service Bus · Event Grid · Foundry · Search · Key Vault · Managed Redis<br/>public endpoints, Entra tokens, no private endpoint"]
    OUT --> RAG["azure-rag-platform on Container Apps<br/>public ingress, Rag.Query app role required"]
```

**There is no peering, in either direction.** The audit expects a diagram with both peerings; the estate
has none to draw. The landing zone deploys no spoke virtual network, no hub, no private endpoint and no
private DNS zone, and its ADR-002 records why: identity is the security boundary for these workloads and
private networking would add cost and operations for no capability they need. The cluster lives in the
virtual network AKS manages for it, every Azure service is reached at its public endpoint with an Entra
token, and the only networking this repository deploys is the default-deny network policy inside the
cluster, where identity cannot stop one pod from calling another (ADR-013).

## Tenant isolation per tier

```mermaid
graph LR
    subgraph starter["Starter · shared"]
        direction TB
        SN["namespace tenants-starter<br/>one gateway deployment for every Starter tenant<br/>HPA 1–3 · ResourceQuota 1 CPU / 1 Gi · priority 1,000"]
        SR["Redis keys quota:{{id}}:* and index idx:cache:{id}"]
        SI["shared index regulations, scoped by the gateway"]
        SC["Cosmos: partition /tenantId in the shared usage container"]
        SK["Key-Vault-signed credential, no Entra object"]
        SN --- SR --- SI --- SC --- SK
    end
    subgraph business["Business · dedicated namespace"]
        direction TB
        BN["namespace t-{id}, own deployment<br/>HPA 2–5 · spot preferred · ResourceQuota 2 CPU / 2 Gi · priority 10,000"]
        BR["Redis keys and cache index of its own"]
        BI["dedicated index t-{id}"]
        BC["Cosmos: container usage-{id}"]
        BK["Entra app registration with the role tenant:{id}, plus the credential"]
        BN --- BR --- BI --- BC --- BK
    end
    subgraph enterprise["Enterprise · dedicated namespace and node pool"]
        direction TB
        EN["namespace t-{id}, own deployment on the enterprise pool<br/>taint and required affinity · HPA 2–10 · ResourceQuota 4 CPU / 4 Gi · priority 100,000"]
        ER["Redis keys and cache index of its own"]
        EI["dedicated index t-{id}"]
        EC["Cosmos: database t-{id}"]
        EK["Entra app registration with the role tenant:{id}, plus the credential"]
        EN --- ER --- EI --- EC --- EK
    end
```

What every tier shares, by construction and by price: the cluster and its control plane, the Gateway and
its proxies, the Managed Redis instance, the Cosmos account, the Service Bus namespace, the Function, the
search service and the RAG platform. ADR-002 prices each boundary; ADR-005 states what serverless Cosmos
cannot isolate; `tests/test_isolation.py` proves, store by store, that a request authenticated as one
tenant cannot read another's record, credential, quota, cached answer, usage or index.

## The request path

```mermaid
sequenceDiagram
    participant C as Customer
    participant G as Gateway pod
    participant R as Managed Redis
    participant L as Azure Language
    participant U as RAG platform
    C->>G: POST /v1/answer with a bearer token
    G->>G: resolve the tenant from the token claim (middleware)
    G->>R: RESERVE the plan's estimated cents (Lua)
    R-->>G: allowed, or 429 with Retry-After
    G->>L: redact when the tenant's policy says so
    G->>R: nearest neighbour in idx:cache:{tenant}
    alt cache hit above the threshold
        G->>R: release the reservation, XADD a usage event at 0 cents
        G-->>C: event verify, x-cache: hit
    else miss
        G->>U: POST /v1/answer as id-saasgw (Rag.Query)
        U-->>G: plan, search, rerank, answer, verify
        G-->>C: the same events, unchanged
        G->>R: SETTLE cost_eur × 100 in fractions of a cent, XADD the usage event
        G->>R: remember the answer in the tenant's index
    end
```

The order is the point: the tenant is known before any store is touched, the reservation precedes every
call that costs money, and the request path never writes a billing row (ADR-003, ADR-004).

## Identities

| Identity | Runs as | Federated subjects |
|---|---|---|
| `id-saasgw` | every tenant gateway and the metering drain | `tenants-starter/gateway`, `platform/meter-drain`, one `t-{id}/gateway` per dedicated tenant, added at onboarding |
| `id-saasfn` | the Function and the KEDA operator | `kube-system/keda-operator`; the Function itself through its own identity binding |
| `id-saasonboard` | the onboarding workers and the operator's shell | `platform/onboarding` |
| `gh-azure-ai-saas-platform` | the four workflows | `ref:refs/heads/main`, `pull_request`, `environment:production` |

`docs/security.md` lists what each one may do and why nothing else is granted.
