# CLAUDE.md — working agreement

`azure-ai-saas-platform` is Regulatory Intelligence as a Service: the regulatory-intelligence
platform in `azure-rag-platform` sold to a hundred companies. It onboards a customer in under two
minutes, isolates customers from one another in compute, data and quota, meters every request as
an event, and attributes cost per customer from two independent sources. A staff engineer reads it
for ten minutes and must conclude the author has thought about what happens when a working system
acquires a hundred paying customers. That depends on **restraint**.

The governing rule:

> If a capability exists in the Azure service or in Kubernetes, do not reimplement it in Python.
> The code that remains is only the code that expresses a business decision.

Its corollaries: **this repository does not reimplement retrieval or generation** — it consumes
`azure-rag-platform` over HTTP against its OpenAPI document and never imports its code (ADR-007);
and **no agent lives in the gateway** (ADR-012) — authenticating, metering, caching, redacting and
forwarding are functions, so `agent_framework` appears nowhere.

Re-read this file at the start of every phase.

## Process

- Do not stage, commit or push. The author commits at the end of each phase with the message
  printed in the phase summary, on a `feature/<words>` branch cut from the previous phase's branch;
  only the last branch is merged into `main`, through a pull request with a merge commit.
- At the end of each phase stop and print:

  ```
  ── PHASE n COMPLETE ──
  Files: <n>/80   Python: <app>/1200 + <support>/600   Bicep: <n>/500   YAML: <n>/500   TS: <n>/700   Comments: <n>/25
  Verified: ruff ✓  mypy ✓  pytest ✓  budget ✓
  Commit with:
    git add -A
    git commit -m "<message>"
    git push -u origin <branch>
  Say "continue" for phase n+1.
  ```

- Every SDK class, method and parameter is verified against the installed package, and every
  Bicep and Kubernetes API version against Microsoft Learn, on the day it is written. The names in
  the tables below were verified on 2026-10-21 against the pinned versions in `requirements*.txt`
  and the Learn reference pages; several differ from the specification and are noted.
- Identity-plane writes (role assignments, Graph writes, federated credentials outside Bicep,
  Cosmos SQL role assignments through the CLI, single-module deployments) are not available to the
  build session. Everything of that kind is declared in Bicep and deployed with the full template;
  what cannot live in Bicep is a script handed to the author.

## Non-negotiable constraints

1. **No identity leakage.** No subscription, tenant, object or client id, email, real hostname or
   portal URL in any committed file, commit message or pull-request comment; all-zero GUIDs are
   placeholders; built-in role-definition ids appear only where Bicep needs them and are allowlisted
   in `.gitleaks.toml`. Tenant fixtures use invented company names.
2. **Zero secrets.** Workload identity and `DefaultAzureCredential` everywhere; `disableLocalAuth`
   or its equivalent on every store; no pod mounts a secret. The one Key Vault key is the
   specification's own exception: it signs the API credentials issued to Starter customers, and
   Cosmos stores only their hashes.
3. **Tenant resolution happens once, at the edge, from a verified token claim** (`app/tenancy.py`),
   never from a header, query parameter or path segment a client controls. A request that cannot be
   attributed to a tenant is rejected before any store is touched.
4. **Quota is enforced before work.** The reservation in Redis precedes the upstream call; the
   reconciliation follows the final event; a failed request releases its reservation.
5. **Metering is an event stream.** The request path writes one `XADD`; nothing on it writes
   billing data synchronously.
6. **Size budget, enforced by `tests/test_budget.py`** (the counting rule is stated below).
7. **Comments.** No `#` comment may restate the code; fewer than 25 in the whole Python codebase.
8. **No defensive clutter.** No bare `except Exception`, no `TODO`, no `utils.py`.
9. **Minimal dependencies**, exact pins; `functions/` is self-contained and carries its own pins.
10. **`targetScope = 'resourceGroup'`.** `rg-aisaas-prod-swc` exists; never create it. No virtual
    network, private endpoint or DNS zone is deployed: identity is the boundary, as the landing
    zone's ADR-002 records; the cluster's managed network and its network policies are the only
    networking here (ADR-013).
11. **Every container** runs non-root, read-only, with capabilities dropped, requests and limits,
    probes, and a pinned image tag. No `latest` anywhere.
12. **LF line endings**, English everywhere, nothing unfinished.

## Budget: the counting rule

The specification's figures are 50 files, 1,200 lines of Python, 500 of Bicep, 500 of YAML and
700 of TypeScript. Its own mandated layout — thirteen Python modules in three packages, eleven
base manifests and three overlays, four workflows, a Function with its host files, a console, load
scripts with a replay stub, three thesis tests — does not fit in 50 files or in 1,200 lines once
the three thesis tests are counted, and every earlier repository in this portfolio split the
ceiling the same way. The rule, recorded here and in the README's scope section:

| Ceiling | Counts | Limit |
|---|---|---|
| Files | tracked files outside `docs/` | 90, raised once in phase 9 from 80: the console needs a manifest, a lockfile and a compiler configuration, the audit requires three k6 scripts, the stub is its own manifest, four workflows are mandated and the node-cost grant is a module of its own |
| Application Python | `app/`, `ops/`, `functions/` | 1,700, raised once in phase 8 from 1,200: the specification budgets 170 lines for onboarding and chargeback, but onboarding converges eight artefacts across Entra, Kubernetes, Resource Manager, Search, Cosmos, Redis, Key Vault and Event Grid with idempotent steps, a queue worker and sealed credential delivery, and chargeback must reconcile two sources and compute unit economics per plan (audit gates 6.6 and 6.7); the gateway modules had already exceeded their table targets for the decisions recorded in phases 5 to 7 |
| Support Python | `tests/`, `load/` | 900, raised once in phase 7 from the 600 proposed in phase 1: the three thesis tests carry their own doubles (a simulated month with its collector and period store, an in-memory vector store, a recorded upstream stream, a PII detector), and the gateway suite plus the load stub did not fit in 600 |
| Bicep | `infra/**/*.bicep`, `*.bicepparam` | 600, raised once in phase 3 from the specification's 500: its own resource list (a cluster with three pools and a data collection rule, Redis, Cosmos with three containers and five data-plane roles, Service Bus with two queues, Event Grid, a Flex Consumption Function with its storage and plan, Key Vault with its key, App Insights, five alerts and a budget, seven diagnostic settings, five federated credentials and twenty-eight role assignments) reached 675 lines in the sibling repositories' style and 535 after compaction |
| YAML | `k8s/**` (the specification's §4 ceiling); workflows are reported, not capped | 500 |
| TypeScript | `console/**/*.ts` | 700 |
| Comments | `#` lines in Python | under 25 |

Functions stay within 35 lines and statements within 120 columns, because `ruff format`
re-expands anything longer. A ceiling is raised only once, flagged as vetoable in the phase block,
with the reason; never silently.

## Architecture in one paragraph

A customer calls the gateway with a token: an Entra token whose app role names the tenant
(Business and Enterprise, one role per tenant on the gateway's registration), or a Key-Vault-signed
API credential issued at onboarding (Starter). `app/tenancy.py` resolves the tenant once and
attaches it to the request; `app/quotas.py` reserves the request's estimated cost in euro cents in
a Redis token bucket and enforces the plan's rate; `app/cache.py` answers from the tenant's own
RediSearch index when a semantically close question was answered before; `app/privacy.py` applies
the tenant's PII policy through Azure Language on the shared Foundry account; `app/upstream.py`
forwards the question to the RAG platform with the gateway's own identity, proxies its
server-sent events unchanged and reads `cost_eur` from the final `verify` event; `app/metering.py`
appends one usage event to a Redis Stream, and its consumer group drains the stream to Service Bus,
where an Azure Function on Flex Consumption aggregates usage into Cosmos under hierarchical
partition keys `/tenantId`, `/month`. `ops/onboard.py` turns a company name and a plan into a
namespace with the tier's quota and network policy, a search index or a scope, a Cosmos partition,
container or database, a Redis bucket and cache index, and a first credential — from the console
through a Service Bus queue that KEDA scales the onboarding workers on, or from the command line.
`ops/chargeback.py` reconciles application metering with Azure Cost Management filtered by tag.

## Decisions recorded before phase 1, with their evidence

| Topic | Decision | Evidence, 2026-10-21 |
|---|---|---|
| Ingress | The managed NGINX of the application routing add-on loses Azure support after November 2026 (upstream ingress-nginx retired, maintenance ended March 2026). The cluster uses the add-on's **Gateway API implementation** (`GatewayClass` `approuting-istio`, `ingressProfile.gatewayAPI.installation: 'Standard'` with `webAppRouting.gatewayAPIImplementations.appRoutingIstio.mode: 'Enabled'`), and `k8s/` carries a `Gateway` and per-tenant `HTTPRoute`s instead of an `Ingress`. Proposed to the author in the phase-1 pre-flight and vetoable until phase 3; the alternative, keeping NGINX for its last month of support, is recorded in ADR-011's scope | Learn `aks/app-routing` retirement notice; `aks/app-routing-gateway-api` |
| Node pools | System pool 1 × `Standard_D4as_v5` (the docs now require 4 vCPUs for system pools; the Gateway API implementation runs two `istiod` replicas and two proxies on it); user pool `Standard_D2as_v5` **spot**, 0–2 nodes, autoscaled; a third pool `enterprise`, regular `Standard_D2as_v5`, 0–1 nodes, tainted `tier=enterprise:NoSchedule`, because the enterprise tier promises no evictions and spot cannot. At rest the cluster is one node. Proposed in the phase-1 pre-flight with its price; vetoable until phase 3 | `aks/use-system-pools`, `aks/spot-node-pool`, `az vm list-usage` |
| Quota | Sweden Central: regional vCPUs 10, every Dv5 family 0, spot 3. The author requests `standardDASv5Family` 12, `lowPriorityCores` 8 and `cores` 16 before phase 3 | `az vm list-usage -l swedencentral` |
| Control-plane tier | Free. Cost analysis needs the Standard tier (≈ 64 € a month) and Kubernetes cost views exist only for EA and MCA offers; this subscription is pay-as-you-go. Per-tenant attribution relies on the `repo` and `tenant` tags and on application metering (`docs/cost.md`) | `aks/cost-analysis` prerequisites and limitations |
| Network | Azure CNI overlay with the Cilium data plane and Cilium network policy; `NetworkPolicy` default-deny per tenant namespace; no virtual network of our own | specification §6, landing zone ADR-002 |
| Redis | Azure Managed Redis `Balanced_B0` (0.5 GB), high availability on, `clusteringPolicy: 'EnterpriseCluster'` and `evictionPolicy: 'NoEviction'` because RediSearch requires both, module `RediSearch`, `accessKeysAuthentication: 'Disabled'`, Entra access-policy assignments. Modules and the clustering policy are fixed at creation: changing them means recreating the database | `redis/redis-modules`, `redis/architecture`, template reference 2026-09-01 |
| Cosmos | Serverless, `disableLocalAuth: true`, no free tier, hierarchical partition keys `/tenantId`, `/month` (`kind: 'MultiHash'`, `version: 2`). Data-plane RBAC cannot create databases or containers, so `id-saasonboard` holds the control-plane role **Cosmos DB Operator** and creates them through ARM; the gateway and the Function hold the data-plane role at account scope so new containers are covered | `cosmos-db/how-to-connect-role-based-access-control` |
| Metering unit | **Euro cents from `cost_eur`**, not tokens: the RAG platform's final `verify` event carries `cost_eur` and `timings_ms` and no token counts, and the platform is closed. The bucket reserves the plan's estimated cents per request and reconciles with the measured cost | `azure-rag-platform/app/models.py` |
| Per-tenant index | The RAG platform answers from one configured index. Dedicated indexes are created and filled at onboarding, and the gateway sends `index` with every upstream question; the platform honours it only after a small change in its own repository, which is the author's pull request. Until then every tenant is answered from the shared index and `docs/security.md` says so | `azure-rag-platform/app/main.py` |
| Scaling the drain | The aggregation workers of the specification are the Flex Consumption Function, which scales on queue length by itself. The in-cluster `ScaledObject` on Service Bus queue length scales the **onboarding workers** (`ops/onboard.py --worker`), which are bursty and idle at zero | KEDA `azure-servicebus` scaler, `aks/keda-workload-identity` |
| Workload identity at scale | A user-assigned identity holds at most 20 federated credentials and subjects admit no wildcard. Dedicated namespaces therefore draw on a **pool of gateway identities** (`id-saasgw`, then `id-saasgw-2`, …), each with identical roles and up to 18 tenant service accounts; ADR-002 does the arithmetic. AKS identity bindings remove the cap (one credential per identity, Kubernetes RBAC authorises service accounts), but the Python SDK supports them only through `WorkloadIdentityCredential(enable_azure_proxy=True)` in a beta of `azure-identity`; the installed 1.26.0 has no such parameter, so that is ADR-002's revisit condition | `entra/workload-id/workload-identity-federation-considerations`, `aks/identity-bindings-concepts`, `inspect.signature(WorkloadIdentityCredential)` |
| Search capacity | The shared Basic service allows 15 indexes and two are taken: 13 dedicated tenants before the service must grow. That bound is in ADR-010 | `search/search-limits-quotas-capacity` |
| PII limits | Synchronous PII detection: 5,120 characters per document and **5 documents per request** (the specification's "1,000" is the language-detection limit). `app/privacy.py` chunks to both | `language-service/concepts/data-limits` |
| Functions | Flex Consumption, Python 3.12, entry point `functions/function_app.py` registering the blueprint in `functions/aggregate.py`; identity-based `AzureWebJobsStorage` and Service Bus connections; the identity needs **Azure Service Bus Data Owner** for the trigger's scale controller | `azure-functions/functions-reference-python`, `flex-consumption-plan`, `functions-bindings-service-bus-trigger` |
| Degradation | The upstream client's circuit breaker opens after `BREAKER_FAILURES` (5) consecutive connection errors, timeouts or 5xx and refuses for `BREAKER_RESET_S` (30 s), then lets one request probe. Open circuit or failed call: 503 `problem+json` with `Retry-After`, the quota reservation released; cached answers are still served because the cache precedes the upstream. The README states exactly this (ADR-007) | `app/upstream.py`, `tests/test_gateway.py` |
| Request order | tenant (middleware) → quota reservation → PII policy → cache → upstream → settle → meter → remember. The reservation precedes every call that costs money, including Language and embeddings; a hit releases it, a failure releases it, a stream settles it with `round(cost_eur × 100)` cents | `app/main.py` |
| Onboarding | Eight idempotent steps in `ops/onboard.py`, each recorded on the tenant document so the console can watch: app registration and `tenant:<id>` app role through Microsoft Graph (dedicated plans), namespace through a generated kustomize wrapper over the tier overlay applied with `kubectl`, federated credential on `id-saasgw` for the namespace's service account, dedicated Search index filled by a copy of the shared skillset and indexer, Cosmos container or database through Resource Manager, Redis bucket and cache index, Key-Vault-signed credential with its hash in Cosmos and its public key in the JWKS document, and a CloudEvent on the platform topic. Starter tenants skip the dedicated steps. Tenants are `provisioning` until the last step, and the resolver serves only `ready` ones | `ops/onboard.py`, `tests/test_operations.py` |
| Credential delivery | The shell prints the credential once. The console sends an RSA public key with the order; the worker seals the credential (AES-GCM, key wrapped with RSA-OAEP), stores only the ciphertext, and the status route returns it once and removes it. No plaintext credential is persisted anywhere (gate 5.5) | `ops/onboard.py`, `app/main.py`, `console/src/main.ts` |
| Graph rights | `id-saasonboard` needs `Application.ReadWrite.OwnedBy` and `AppRoleAssignment.ReadWrite.All` on Microsoft Graph and must own the gateway's registration to add `tenant:<id>` roles to it. The registration itself (roles `Platform.Admin` and `tenant:*`, delegated scope `Console.Access`, SPA redirect, v2 tokens) and the `Rag.Query` grant to `id-saasgw` are the author's bootstrap script, because Graph writes live outside Bicep and outside this session | `p04-graph-grants.sh`, outside the repository |
| Chargeback | Cost Management query at resource-group scope grouped by service name and the `tenant` tag, filtered by the `repo` tag, against the `BillingPeriod` documents the Function keeps; shared cost is allocated by each tenant's share of metered cents; the report prints per-tenant lines, per-plan unit economics and the gap statement | `ops/chargeback.py` |
| Load stub | `load/stub.yaml`: unprivileged nginx in `platform` replaying the recorded `/v1/answer` stream with `cost_eur` 0.01 through an nginx `return`, which answers any method; not Python, because the support ceiling has no headroom and a static replay measures the gateway better than a process would. The phase-4 network policies already allowed the path (`upstream-stub`, port 8080). nginx config strings honour `\n` escapes | `load/stub.yaml`, `k8s/base/networkpolicy.yaml` |
| Quota switch | `ENFORCE_QUOTA` (default true) lets the noisy-neighbour run compare the protected tenant's p95 with and without enforcement; `app/main.py::reserve` returns no reservation when it is off and the release and settle paths tolerate that. A deployment setting toggled by `load.yml` with `kubectl set env`, never a request parameter | `app/config.py`, `.github/workflows/load.yml` |
| Cache resilience | an embedding failure is a cache miss and nothing is remembered; the request proceeds to the upstream. Under a flood the shared embedding deployment is the first dependency to throttle, and the gateway must not turn that into 500s | `app/cache.py` |
| Workflows | `ci` (ruff, format, mypy, pytest, budget, Bicep build and lint, console build, kubeconform on every kustomization and the stub, gitleaks over the full history, trivy fs); `deploy` builds with `docker build` and pushes with AcrPush because `az acr build` needs Contributor on the registry (the RAG platform's lesson), renders the wrappers from the deployment outputs, applies platform and starter, waits for the rollouts and undoes the ones that fail; the template itself is deployed from a workstation; `load` points the load tenants at the stub, runs the scenarios, toggles `ENFORCE_QUOTA`, restores both and returns the results as an artifact and the job summary, and the author commits what is worth publishing because nothing pushes to `main`; `chargeback` runs monthly and returns the report as an artifact. Action tags verified on GitHub on 2026-10-21: checkout v7, setup-python v7, setup-node v7, upload-artifact v7, azure/login v3, azure/setup-kubectl v5, azure/use-kubelogin v1 with kubelogin v0.2.21, grafana/setup-k6-action v1.2.2, aquasecurity/trivy-action v0.36.0, gitleaks v8.30.1 | `.github/workflows/` |
| Node costs | AKS bills the nodes in `rg-aisaas-prod-swc-nodes`, outside the template's scope, so `ops/chargeback.py` queries both groups and `infra/modules/nodes.bicep` grants Cost Management Reader there to the CI principal and the operator through a module scoped to that group, after the cluster exists | `infra/main.bicep` |
| Deploy-time wrappers | `k8s/render.sh` writes `k8s/.generated/{starter,platform}` from the deployment outputs: the image, the service-account client ids (`id-saasgw` for the starter gateway and the drain, `id-saasonboard` for onboarding), the KEDA identity (`id-saasfn`, whose federation covers `kube-system/keda-operator`) and `settings.env`; `make deployment-env` turns the outputs into upper-snake-case variables. Verified locally with kubeconform in phase 9 | `k8s/render.sh`, `Makefile` |
| Key Vault | RBAC authorisation, purge protection on (irreversible: a destroy-and-redeploy recovers the soft-deleted vault rather than recreating the name). `id-saasonboard` holds Key Vault Crypto User (sign); the gateway verifies signatures with the public key that onboarding publishes to Cosmos and holds no Key Vault role at all | specification §6, `az role definition list` |

## API versions verified on 2026-10-21

The authoritative list of a type's versions is the resource provider itself (`az provider show
--query "resourceTypes[?resourceType=='<type>'].apiVersions"`), not the Learn page, whose version
menu is rendered by script and once yielded a Cosmos version that does not exist. The templates use
the newest GA version for which Bicep 0.48.1 ships type definitions, so every property name is
validated at build; where the provider has a newer GA version without types, it is noted.

`Microsoft.ContainerService/managedClusters` 2026-06-01 (provider: 2026-08-01) ·
`Microsoft.Cache/redisEnterprise`, `/databases`, `/databases/accessPolicyAssignments` 2025-07-01
(provider: 2026-09-01) · `Microsoft.DocumentDB/databaseAccounts` and children 2025-10-15 (provider:
2026-03-15) · `Microsoft.ServiceBus/namespaces` and `/queues` 2026-01-01 ·
`Microsoft.EventGrid/topics` 2025-02-15 · `Microsoft.Web/serverfarms` and `/sites` 2025-03-01
(provider: 2026-08-01) · `Microsoft.Storage/storageAccounts` and `/blobServices/containers`
2026-06-01 (provider: 2026-09-01) · `Microsoft.KeyVault/vaults` and `/keys` 2026-05-15 ·
`Microsoft.Insights/components` 2020-02-02 · `Microsoft.Insights/dataCollectionRules` and
`/dataCollectionRuleAssociations` 2024-03-11 · `Microsoft.Insights/metricAlerts` 2026-01-01 ·
`Microsoft.Insights/diagnosticSettings` 2021-05-01-preview (the version every portfolio template
uses; the provider lists no newer one) · `Microsoft.ManagedIdentity/userAssignedIdentities` and
`/federatedIdentityCredentials` 2024-11-30 · `Microsoft.Authorization/roleAssignments` 2022-04-01 ·
`Microsoft.Consumption/budgets` 2026-06-01 · existing references:
`Microsoft.ContainerRegistry/registries` 2025-11-01, `Microsoft.CognitiveServices/accounts`
2026-07-01, `Microsoft.Search/searchServices` 2025-05-01.

Kubernetes 1.36 (the AKS default in Sweden Central), validated with `kubeconform` against
`v1.36.0-standalone-strict` and the KEDA and Gateway API schemas of the CRDs catalog.

## Kubernetes manifests, verified in phase 4

- API versions: `apps/v1` Deployment, `v1` Namespace, ServiceAccount, Service, ResourceQuota,
  LimitRange, ConfigMap, `networking.k8s.io/v1` NetworkPolicy, `scheduling.k8s.io/v1`
  PriorityClass, `autoscaling/v2` HorizontalPodAutoscaler, `rbac.authorization.k8s.io/v1`
  ClusterRole and ClusterRoleBinding, `gateway.networking.k8s.io/v1` Gateway and HTTPRoute (the
  managed Gateway API installation, class `approuting-istio`), `keda.sh/v1alpha1`
  TriggerAuthentication and ScaledObject. Every kustomization builds with
  `kubectl kustomize --load-restrictor LoadRestrictionsNone` and passes `kubeconform -strict` with
  the 1.36.0 schemas plus the CRDs catalog: 15 objects per tier overlay, 14 in the platform.
- **Layout.** `k8s/base` is one tenant namespace's stack; the three overlays differ only in what
  the tier model says (namespace, quota, limits, replicas and autoscaling bounds, priority class,
  resources, the route prefix with its `URLRewrite`, and for enterprise the toleration and the
  required affinity to the `enterprise` pool instead of the preferred affinity to spot). The starter
  overlay is the real shared namespace `tenants-starter`; business and enterprise are templates whose
  `t-tenant` namespace and `/t/tenant` prefix the onboarding replaces per customer. `k8s/platform`
  holds the shared `Gateway`, the metering drain, the onboarding workers with their KEDA
  `ScaledObject` on the Service Bus `onboarding` queue, and the platform network policies; the load
  stub lives under `load/` because it is test tooling.
- **Nothing environment-specific is committed.** Images are `gateway:0.0.0` and the kustomize
  `images` transformer sets the registry and tag at deploy time; service-account client ids and the
  KEDA `identityId` are all-zero placeholders patched at deploy time; `k8s/settings.env` holds
  `.invalid` hosts and is replaced by a `configMapGenerator` with `behavior: replace` in the
  generated wrapper kustomization (`k8s/.generated/`, gitignored). `settings.env` sits above the
  kustomizations so base and platform share it, which is why every build passes
  `--load-restrictor LoadRestrictionsNone`.
- **Per-tenant namespaces are the onboarding's wrapper.** `ops/onboard.py` writes
  `<workdir>/<namespace>/kustomization.yaml` (JSON, which kustomize reads) over the tier overlay with `namespace`,
  the image, the route prefix, the service-account client id and a replaced `settings.env` carrying `TENANT_SCOPE`,
  renders it with `kubectl kustomize --load-restrictor LoadRestrictionsNone` and applies the output; the namespace
  transformer renames the `Namespace` object as well (verified locally in phase 8). In the cluster the wrapper lands
  in `/tmp/onboard` because the root filesystem is read-only, and the image carries `kubectl` pinned by version and
  checksum for that one purpose.
- **Security shape.** Namespaces carry `pod-security.kubernetes.io/enforce: restricted`; every pod
  runs as user 10001, non-root, read-only root filesystem, all capabilities dropped, no privilege
  escalation, `RuntimeDefault` seccomp, `emptyDir` on `/tmp`, requests and limits, readiness and
  liveness probes (HTTP on the gateway, a heartbeat file the workers touch for the drain and the
  onboarding worker); the gateway and the drain do not mount the API token. The default-deny
  policy is an empty `podSelector` with both policy types; the only ingress to a tenant gateway is
  from the `ingress` namespace, where the Gateway's proxies run (it enforces `baseline`, because the
  AKS-managed proxy sets no seccomp profile); egress is DNS, the public Azure
  endpoints on 443 and 10000 with the private ranges excluded, and the load stub.
- **Tier arithmetic.** Starter: quota 1 CPU / 1 Gi requests, 2 / 2 Gi limits, 10 pods, HPA 1–3;
  Business: 2 / 2 Gi, 4 / 4 Gi, 20 pods, HPA 2–5, priority 10,000; Enterprise: 4 / 4 Gi, 8 / 8 Gi,
  40 pods, HPA 2–10, priority 100,000 with preemption, dedicated pool. Each pod is sized so that the HPA
  ceiling fits the quota: Starter 100m / 256Mi requests and 500m / 512Mi limits, Business 250m / 384Mi and
  750m / 768Mi, Enterprise 400m / 400Mi and 800m / 800Mi (a Business pod at one CPU stopped the fifth
  replica at the quota in the first load run). `docs/architecture.md` draws
  it and `tests/test_isolation.py` exercises the data stores in phase 5.

## SDK reference — verified against the installed packages

| Purpose | Verified names |
|---|---|
| Settings | `pydantic_settings.BaseSettings` with `SettingsConfigDict(env_file=".env", extra="ignore")`; every endpoint is a required field with no default |
| Models | `pydantic.BaseModel`, `Field`, `enum.StrEnum` |
| Identity | `azure.identity.DefaultAzureCredential`, `azure.identity.aio.DefaultAzureCredential`; on AKS the workload identity webhook injects `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_FEDERATED_TOKEN_FILE`, and `WorkloadIdentityCredential` is part of the default chain |
| Tokens | `jwt.PyJWKClient(uri, cache_keys=True).get_signing_key_from_jwt(token).key`; `jwt.decode(token, key, algorithms=["RS256"], audience=, issuer=)` raises `jwt.PyJWTError`; `jwt.get_unverified_header(token)["kid"]`; `jwt.decode(token, options={"verify_signature": False})` to read the issuer before choosing the verifier; `jwt.PyJWK.from_dict(jwk).key`; `jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)` to publish a key; `jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": ...})` (verified in phase 5) |
| Redis | `redis.asyncio.Redis(host=, port=, ssl=True, username=, credential_provider=, decode_responses=True, protocol=3)`; `redis.register_script(lua)` returns an `AsyncScript` awaited as `script(keys=[...], args=[...])`; `await redis.time()` → `(seconds, microseconds)`; `redis.ft(index_name)` for RediSearch (phase 6); `redis.credentials.CredentialProvider.get_credentials()`/`get_credentials_async()` for Entra tokens. Lua numbers returned to Redis are truncated to integers, so the scripts return `math.floor` cents and keep the fractional bucket in the hash. `fakeredis.aioredis.FakeRedis(decode_responses=True)` with `fakeredis[lua]` runs the same scripts in the tests (verified in phase 5) |
| Cosmos | `azure.cosmos.aio.CosmosClient(url, credential)` → `get_database_client(name).get_container_client(name)` → `ContainerProxy.read_item(item=, partition_key=)` (a list for hierarchical keys), `upsert_item(body)`, `patch_item(item=, partition_key=, patch_operations=[{"op": "incr", "path": "/requests", "value": 1}])`, `query_items(query=, parameters=)`; `azure.cosmos.exceptions.CosmosResourceNotFoundError` (verified in phase 5) |
| Edge | `starlette.datastructures.Headers(scope=)`, `starlette.responses.JSONResponse(content, status_code, headers, media_type)`, pure-ASGI middleware over `starlette.types.ASGIApp, Scope, Receive, Send`, because `BaseHTTPMiddleware` buffers streaming responses and the gateway streams server-sent events; `starlette.testclient.TestClient` in the tests |
| Streams | `redis.xadd(stream, fields, maxlen=, approximate=True)` rejects `bool` values (`UsageEvent.fields()` turns them into ints); `xgroup_create(stream, group, id="0", mkstream=True)` raises `ResponseError` `BUSYGROUP` when the group exists and must start at `"0"`, because `"$"` skips every entry recorded before the drain started (measured); `xreadgroup(group, consumer, {stream: ">"}, count=, block=)` → `[[stream, [(id, fields), ...]]]` on RESP2; `xautoclaim(stream, group, consumer, min_idle_time=, count=)` → `[next_id, [(id, fields), ...], [deleted]]`; `xack(stream, group, *ids)`. `fakeredis` supports all of them and none of `FT.*` (verified in phase 6) |
| Redis auth | `redis_entraid.cred_provider.create_from_default_azure_credential(("https://redis.azure.com/.default",))` returns a `StreamingCredentialProvider` whose refreshed token redis-py re-sends with `AUTH <oid> <token>` on live connections; RESP2 is enough, so the client keeps the default protocol (verified in phase 6) |
| Semantic cache | `redis.ft("idx:cache:<tenant>").create_index([TextField(...), VectorField("embedding", "HNSW", {"TYPE": "FLOAT32", "DIM": 1536, "DISTANCE_METRIC": "COSINE"})], definition=IndexDefinition(prefix=["cache:<tenant>:"], index_type=IndexType.HASH))`; `.search(Query("*=>[KNN 1 @embedding $vec AS distance]").return_fields("answer", "distance").sort_by("distance").dialect(2), query_params={"vec": <float32 bytes>})` → `result.docs[0].answer/.distance`; `IndexDefinition` is untyped in redis-py 8 (one inline ignore). Embeddings: `POST {foundry}/openai/deployments/{deployment}/embeddings?api-version=2024-10-21` with a bearer for `https://cognitiveservices.azure.com/.default` |
| Service Bus | `azure.servicebus.aio.ServiceBusClient(fully_qualified_namespace, credential)` as an async context manager; `get_queue_sender(queue).send_messages([ServiceBusMessage(body, content_type=)])` sends a batch; the Basic tier caps a message at 256 KB and a usage event is about 200 bytes |
| PII | `azure.ai.textanalytics.aio.TextAnalyticsClient(endpoint, credential)` → `await client.recognize_pii_entities(documents, language="en")` → items with `.is_error`, `.redacted_text`, `.entities` (each with `.category`, `.confidence_score`) or `.error.message`; the Protocol the gateway types against declares `(documents, **kwargs) -> Awaitable[Any]` so both the real client and the test double satisfy it (verified in phase 7) |
| Upstream | `httpx.AsyncClient.build_request("POST", url, json=, headers=, timeout=httpx.Timeout(90, connect=5))` then `await client.send(request, stream=True)` → `response.aiter_lines()` yields server-sent-event lines without their terminators, so each is re-emitted with `"\n"` and an empty line closes a block; `httpx.MockTransport(handler)` and `httpx.ASGITransport(app=)` drive the gateway in the tests inside one event loop, which the thread-based `TestClient` cannot do with async fakes (verified in phase 7) |
| Telemetry | `configure_azure_monitor(connection_string=, credential=DefaultAzureCredential(), resource=Resource.create({"service.name": ...}))`; the distro pulls the FastAPI and httpx instrumentations, so requests and upstream calls become App Insights requests and dependencies; `opentelemetry.sdk.trace.SpanProcessor.on_start(span, parent_context)` stamps `tenant_id` from a `ContextVar` the route sets, and `trace.get_tracer_provider().add_span_processor(...)` registers it after the distro's provider exists (verified in phase 7) |
| Key Vault keys | `azure.keyvault.keys.aio.KeyClient(vault_url, credential).get_key(name)` → `.properties.version`, `.key.n`, `.key.e` as bytes; `azure.keyvault.keys.crypto.aio.CryptographyClient(key_id, credential).sign(SignatureAlgorithm.rs256, digest)` → `.signature`; the JWT is assembled by hand because PyJWT cannot sign through a vault (verified in phase 8) |
| Search admin | `azure.search.documents.indexes.aio.SearchIndexClient.get_index(name)` / `create_index(index)`; `SearchIndexerClient.get_skillset` / `create_skillset` / `get_indexer` / `create_indexer` / `run_indexer`; the RAG platform's vector field is `hidden`, so a dedicated index is filled by copying the skillset and indexer with the new target, never by copying documents; 409 means already there (verified in phase 8) |
| Event Grid | `azure.eventgrid.aio.EventGridPublisherClient(endpoint, credential).send(CloudEvent(source=, type=, subject=, data=))` on the topic's `CloudEventSchemaV1_0` (verified in phase 8) |
| Service Bus receive | `get_queue_receiver(queue, auto_lock_renewer=AutoLockRenewer(max_lock_renewal_duration=900))` → `receive_messages(max_message_count=1, max_wait_time=30)`, `complete_message`, `dead_letter_message(message, reason=, error_description=)`; `str(message)` is the body (verified in phase 8) |
| Graph and Resource Manager | plain `httpx` with `DefaultAzureCredential` tokens for `https://graph.microsoft.com/.default` and `https://management.azure.com/.default`: `applications(appId='…')`, `PATCH applications/{id}` with the whole `appRoles` list, `servicePrincipals(appId='…')`, `POST servicePrincipals/{id}/appRoleAssignedTo`; ARM `PUT` with `Azure-AsyncOperation` polling and the Bicep-typed versions (Cosmos `2025-10-15`, federated credentials `2024-11-30`); Cost Management `query?api-version=2026-06-01` grouped by `ServiceName` and `TagKey tenant` (Learn, 2026-10-21) |
| Sealing | `cryptography` AES-GCM (`AESGCM.generate_key`, `.encrypt(nonce, data, None)`) and RSA-OAEP SHA-256, matched by WebCrypto `RSA-OAEP` and `AES-GCM` in the console; RSA-OAEP alone cannot hold a 400-byte credential (a 2048-bit key seals 190 bytes), which the test caught in phase 8 |
| k6 | scenarios API: `ramping-arrival-rate` with stages, `constant-arrival-rate` with `exec` per scenario and tags, `per-vu-iterations`; thresholds on tagged metrics such as `http_req_duration{scenario:protected,expected_response:true}`; `handleSummary(data)` returns a map of file paths to content; `Trend` custom metrics (k6 1.8.2 docs, 2026-10-21) |
| kubectl | `kubectl set env deployment/x KEY=value` adds an explicit env that wins over `envFrom`, and `KEY-` removes it; `kubectl get gateway saas -o jsonpath='{.status.addresses[0].value}'` is the Gateway API address; `kubectl kustomize --load-restrictor LoadRestrictionsNone <dir> | kubectl apply -f -` because `apply -k` has no load-restrictor flag (verified locally in phase 8) |
| Functions | `azure.functions.Blueprint()` (untyped constructor, one inline ignore) with `@bp.service_bus_queue_trigger(arg_name=, queue_name="%USAGE_QUEUE%", connection="ServiceBusConnection", cardinality="many")` receiving `list[func.ServiceBusMessage]` whose `.get_body()` is bytes; `function_app.py` imports `aggregate` as a top-level module because the host runs the package root, so mypy excludes that five-line file; the sync `azure.cosmos.ContainerProxy.patch_item(item=, partition_key=[tenant, month], patch_operations=[{"op": "incr", ...}])` and `create_item(body)` with `CosmosResourceNotFoundError`/`CosmosResourceExistsError` on the race (verified in phase 6) |

Later phases extend this table as each module is written; nothing is written against a name that
has not been imported and introspected first.

## Infrastructure facts verified while writing `infra/`

- Phase 2 (identities, Key Vault, App Insights, shared-service roles): `az bicep build` and `lint`
  clean on every template with Bicep 0.48.1; `az deployment group what-if` succeeded with 14
  creates and 8 role assignments reported as unsupported because their names derive from runtime
  principal ids (the same report every earlier repository produced). The parameters file reads the
  azd environment, which lives in the gitignored `.azure/` and is exported into the shell with
  `eval "$(azd env get-values | sed 's/^/export /')"`. A role assignment loop whose collection
  contains a resource property fails with BCP178, so the two assignments on `id-saasgw` are
  separate resources, while loops inside modules iterate over module parameters freely.
- `Microsoft.Insights/components` with `DisableLocalAuth: true` makes the connection string a
  locator, not a secret: every sender needs Monitoring Metrics Publisher on the component, which
  the template grants to the three identities and the operator.
- The Key Vault is `createMode: 'default'` on first deployment and `'recover'` (`VAULT_CREATE_MODE`)
  after a destroy, because purge protection keeps the soft-deleted vault for 90 days. Deployed on
  2026-10-21 in 37 seconds.
- Phase 3 (cluster, data plane, Function, alerts, federation): every module takes one `common`
  object (location, tags, suffix, workspace id) and resources are written with several properties
  per line, which brought the templates from 675 to 556 lines. **Measured:** a module parameter
  that is an *object* containing a `reference()` (a principal id of an identity created in the
  same template) makes what-if short-circuit the whole nested deployment
  (`NestedDeploymentShortCircuited`), while the same ids passed as *string* parameters are
  evaluated, so principal ids travel as strings and `common` carries only deploy-time values.
  With that, what-if reaches the AKS preflight, which rejected `upgradeSettings.maxSurge` on the
  spot pool ("Spot pools can't set max surge") and then stopped on the DASv5 quota, which is the
  author's pending request; the rest of the template was validated with node sizes from a family
  that has quota. `metricAlerts` criteria need `criterionType:
  'StaticThresholdCriterion'`. The Service Bus name trips BCP334 on the six-character minimum because
  Bicep cannot see the interpolated length; the one `#disable-next-line` in the repository is there.
  The Container insights data collection rule carries the extension `ContainerInsights` with five
  streams, a five-minute interval and the system namespaces excluded, and its association must be
  named `ContainerInsightsExtension`. Kubelet pulls images with the cluster's own kubelet identity,
  so `AcrPull` goes to `identityProfile.kubeletidentity.objectId`, not to a workload identity. The
  federated credentials to Kubernetes service accounts live in `modules/federation.bicep` because
  they need the cluster's OIDC issuer URL; per-tenant namespaces add theirs at onboarding.

## Phases

| # | Work | Commit message |
|---|---|---|
| 1 | `CLAUDE.md`, scaffold, tooling, `config.py`, `models.py`, `test_budget.py` | `chore: scaffold multi-tenant platform repository` |
| 2 | `infra/`: identities, workload identity federation, Key Vault, App Insights | `feat(infra): identities and workload identity federation` |
| 3 | `infra/`: AKS with node pools and add-ons, Redis, Cosmos, Service Bus, Key Vault, alerts | `feat(infra): aks cluster, redis and tenant data plane` |
| 4 | `k8s/` base and three overlays, quotas, network policies, KEDA | `feat(k8s): namespace isolation, quotas and autoscaling` |
| 5 | `tenancy.py`, `quotas.py`, isolation tests, ADR-002, ADR-003 | `feat(tenancy): tenant resolution and quota enforcement` |
| 6 | `metering.py`, `functions/aggregate.py`, `cache.py`, ADR-004, ADR-006 | `feat(metering): usage streams, aggregation and per-tenant cache` |
| 7 | `privacy.py`, `upstream.py`, `main.py`, `telemetry.py`, ADR-007, ADR-008 | `feat(gateway): pii policy, upstream client and api surface` |
| 8 | `ops/onboard.py`, `ops/chargeback.py`, admin console, onboarding GIF | `feat(operations): tenant onboarding and cost attribution` |
| 9 | `load/`, benchmarks, workflows, `slo.md`, all docs and ADRs, README | `ci: load testing, slo documentation and deployment` |

## Definition of done

Budget green with actual counts · ruff, mypy, pytest clean · Bicep clean and `kubeconform` passes
on every manifest of every overlay · the cross-tenant isolation test passes for every store ·
the quota test proves no upstream call is made when a tenant is at its ceiling · metering accuracy
within tolerance over a simulated month · no container runs as root or with a `latest` tag · no
email or GUID outside placeholders · comment count under 25, reported · `docs/cost.md` reconciles
application metering against Azure Cost Management and explains the gap · nothing committed or
pushed. Then list what could not be verified without a live cluster.
