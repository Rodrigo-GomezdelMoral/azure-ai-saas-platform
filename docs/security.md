# Security

Identity is the security boundary. There is no spoke network, peering, private endpoint or private DNS
zone in this estate, and the landing zone's ADR-002 records why; the only networking this repository
deploys is the default-deny network policy inside the cluster (ADR-013).

## The one secret

Key Vault holds exactly one thing with a secret purpose: the RSA-3072 key `credential-signing`, which
signs the API credentials issued to customers at onboarding. The key never leaves the vault
(`id-saasonboard` holds Key Vault Crypto User, sign only, no export and no Secrets role); the gateway
verifies signatures with the public key the onboarding publishes to Cosmos and holds no Key Vault role
at all. Cosmos stores the SHA-256 hash of every issued credential, never the credential
(`app/tenancy.py`, `ops/onboard.py`). The console receives a credential sealed to a key that exists only
in the operator's browser session; the platform keeps ciphertext it cannot open and discards it on the
first read (`app/main.py`, `console/src/main.ts`). Every other access, from every identity, is an Entra
token: workload identity in the cluster, `DefaultAzureCredential` in the code, no client secret anywhere
and no secret mounted into any pod (`k8s/base/serviceaccount.yaml` does not even mount the API token).

Every token is verified for signature, audience, issuer and expiry with five minutes of clock skew, the
default of Microsoft.IdentityModel; a credential signed on a workstation whose clock runs ahead of the
cluster's would otherwise be refused as not yet valid, which is what happened on the first live run
(`app/tenancy.py`).

## Identities and roles

| Identity | Roles, each declared in `infra/` unless noted | Why nothing more |
|---|---|---|
| `id-saasgw` (tenant gateways, metering drain) | Cosmos DB data contributor at account scope; Redis access policy; Service Bus Data Sender; Event Grid Data Sender; Cognitive Services User and Cognitive Services OpenAI User on the shared Foundry account; Monitoring Metrics Publisher; app role `Rag.Query` on the RAG platform's API (Graph, bootstrap script) | it reads tenants and credentials, meters, caches, redacts and forwards; it creates nothing and signs nothing |
| `id-saasfn` (Function, KEDA operator) | Cosmos DB data contributor; Service Bus Data Owner, which the trigger's scale controller requires; Storage Blob Data Owner on the Function's own storage | it folds usage events into billing documents |
| `id-saasonboard` (onboarding workers, operator shell) | Cosmos DB data contributor and Cosmos DB Operator (control plane, to create containers and databases); Service Bus Data Receiver; Event Grid Data Sender; Redis access policy; Key Vault Crypto User; Search Service Contributor and Search Index Data Contributor; Managed Identity Contributor on `id-saasgw` only, to add federated credentials; Graph application permissions `Application.ReadWrite.OwnedBy` and `AppRoleAssignment.ReadWrite.All` and ownership of the gateway's registration (bootstrap script) | it creates the eight artefacts of a tenant and nothing else |
| `gh-azure-ai-saas-platform` (CI) | AcrPush; AKS RBAC Cluster Admin and Cluster User; Cost Management Reader on the platform group and the node group; Cosmos data reader; app role `Platform.Admin` for the onboarding load test (bootstrap script) | it builds, applies, reads costs and runs load tests |
| the operator | Key Vault Crypto User; Cosmos DB Operator; Event Grid Data Sender; Managed Identity Contributor on `id-saasgw`; Cognitive Services User; Redis access policy; AKS RBAC Cluster Admin and User; Cost Management Reader on the node group; app role `Platform.Admin` | onboarding from a workstation and the console |
| the kubelet identity | AcrPull on the shared registry | pulls the image |

The role definition ids are in `infra/modules/*.bicep` and allow-listed in `.gitleaks.toml`; no other
GUID exists in the repository. Three federated credentials cover CI: the `main` branch, pull requests
and the `production` environment, because a job that runs in an environment presents the
`environment:<name>` subject and Entra answers `AADSTS700213` when no credential matches it.

## Multi-tenancy threats

| Threat | Mitigation | Test that proves it |
|---|---|---|
| Cross-tenant data access: a request by tenant A reads B's record, credential, quota, cached answer, usage or index | the tenant comes from the token's app role or the platform credential's subject only, never from a header, a path or a query string (`app/tenancy.py`); a dedicated gateway refuses tokens of other tenants (`TENANT_SCOPE`); every Redis key and cache index carries the tenant; Cosmos partitions, containers and databases are per tenant by plan (`app/models.py`) | `tests/test_isolation.py::test_tenant_a_cannot_reach_tenant_b`, parameterised over the six stores; `tests/test_tenancy.py::test_a_dedicated_gateway_refuses_other_tenants`; `tests/test_metering.py::test_redisearch_keys_and_index_carry_the_tenant` |
| Quota bypass: work done, and paid for, beyond the plan | the reservation precedes every call that costs money; rate, burst and monthly ceilings are one atomic Lua script (`app/quotas.py`, `app/main.py::reserve`); failure to reserve is a 429 before the upstream is touched; the `ENFORCE_QUOTA` deployment setting defaults on and exists only for the noisy-neighbour comparison | `tests/test_gateway.py::test_tenant_at_its_ceiling_is_rejected_before_any_upstream_call`, which asserts on the upstream recorder, not on a status code; `tests/test_quotas.py`, five cases |
| Noisy neighbour: one tenant's traffic degrades another's | quotas at the door; per-namespace `ResourceQuota`, `LimitRange`, HPA bounds and `PriorityClass` that differ by tier (`k8s/overlays/*`); the Enterprise pool and taint; default-deny network policy between namespaces | `tests/test_quotas.py::test_rate_limit_rejects_within_the_minute`; `docs/benchmarks/2026-10-25-load.md`: under a Starter flood the protected tenant answered 181 of 181, p95 524 ms with enforcement and 596 ms without, one run each |
| Credential leakage between namespaces | one service account per namespace with its own federated subject on `id-saasgw` (`infra/modules/federation.bicep`, `ops/onboard.py::federate`), no secret and no API token mounted into gateway pods, credentials stored as hashes and delivered sealed, signing keys never leave the vault | `tests/test_tenancy.py::test_bad_credentials_are_rejected` (forged, unknown, revoked, wrong key id); `tests/test_operations.py::test_business_onboarding_creates_every_artefact` asserts the issued credential appears in no stored document; `tests/test_operations.py::test_starter_tenant_gets_a_sealed_credential_and_no_control_plane_calls` |
| Billing manipulation: a tenant shifts cost to another or under-reports | the tenant on every usage event is the resolved tenant; the cost is read from the platform's final `verify` event only (`app/upstream.py`, `app/main.py::relay`); metering is at-least-once with bounded duplicates and reconciled against Azure Cost Management by tag (`ops/chargeback.py`) | `tests/test_gateway.py::test_answer_streams_upstream_events_unchanged_and_meters_the_final_event`; `tests/test_metering.py::test_metering_accuracy_over_a_simulated_month`; `tests/test_operations.py::test_chargeback_reconciles_both_sources_and_explains_the_gap` |
| Unattributed requests reaching a store | the middleware resolves the tenant before routing and answers 401 in `problem+json` without touching any store | `tests/test_tenancy.py::test_middleware_rejects_before_routing`; `tests/test_gateway.py::test_unauthenticated_requests_touch_nothing` |
| Tenant reaching the admin surface | `/admin/*` is not on the public listener (the starter route carries `/v1` only; operators use a port-forward) and requires the `Platform.Admin` app role, which no tenant role grants (`app/main.py::admin`) | `tests/test_gateway.py::test_admin_role_queues_a_burst_of_onboardings_and_tenants_cannot` |

Every file and test named above exists in this repository; `pytest -q` runs all of them in seconds.

## Data handling

- The PII policy is per tenant (ADR-008): `before_upstream` sends the redacted question to the platform
  and keeps the redacted text; `before_storage` sends the original and keeps the redacted text; `none`
  keeps the original. Redaction uses Azure Language on the shared Foundry account within its documented
  limits, and a failed redaction for a tenant that requires it ends the request with 502 rather than a
  leak (`app/privacy.py`, `tests/test_gateway.py::test_pii_policy_decides_what_leaves_and_what_is_kept`).
- The per-tenant index is sent with every upstream question; the RAG platform honours it only after a
  small change in its own repository, which is the author's pull request. Until then every tenant is
  answered from the shared `regulations` corpus, which is public regulation and contains no customer data.
- Telemetry carries `tenant_id` on every span and never the question text; Application Insights
  authenticates with Entra (`DisableLocalAuth`).
- Load tests run against an in-cluster stub, never against the platform, and the load tenants run with
  the PII policy `none` (`docs/benchmarks/`).
