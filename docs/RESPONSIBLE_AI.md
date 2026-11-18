# Responsible AI impact assessment

Regulatory Intelligence as a Service's impact assessment follows the sections of the Responsible AI Impact Assessment
Template and the goals of the [Microsoft Responsible AI Standard](https://www.microsoft.com/ai/principles-and-approach),
together with the responsible AI guidance of the [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
for [AI workloads](https://learn.microsoft.com/azure/well-architected/ai/responsible-ai).

## 1. System information

| | |
|---|---|
| **System** | Regulatory Intelligence as a Service: a multi-tenant gateway on Azure Kubernetes Service in front of a RAG platform ([`docs/architecture.md`](architecture.md)) |
| **Layer of the AI stack** | a platform layer between tenants and an AI service. The models, the retrieval, the agents and the answers are upstream, consumed over HTTP against a contract (ADR-007); the gateway holds no agent (ADR-012). Its own AI calls are two: `text-embedding-3-small` embeddings for the semantic cache (ADR-006) and Azure Language PII detection (ADR-008) |
| **What it does** | resolves the tenant from the token claim, reserves quota, redacts by the tenant's policy, answers from the tenant's cache or relays the upstream's event stream unchanged, then settles the quota and meters the cost |
| **Who builds on it** | tenant companies on the Starter, Business and Enterprise plans, whose applications call `/v1/answer` with a token; the operator, who onboards tenants, deploys and bills. The people who read the answers use a tenant's application and never see this layer |
| **Environment and language** | one environment, `production`, first deployed on 2026-10-24, with the cluster workloads delivered by `deploy.yml`; English for the API's errors, the operator console and PII detection (`app/privacy.py`) |

## 2. Intended uses

1. **Answering a tenant's question within its plan.** A tenant's application sends a regulatory question
   and receives the RAG platform's answer stream, within the tenant's quota, PII policy and cache.
2. **Onboarding and billing a tenant.** An operator creates a serving tenant in eight idempotent steps
   ([`docs/onboarding.md`](onboarding.md)) and bills it from metered usage reconciled against billed cost ([`docs/cost.md`](cost.md)).

**Goals of the Responsible AI Standard that apply:**

- *Impact assessment (A1):* this document, kept next to the ADRs and benchmarks it cites.
- *Fit for purpose (A3):* each purpose of the layer is a claim with a test or a measurement behind it
  (README, *What this demonstrates*); the objectives it misses are published with the benchmarks.
- *Data governance and management (A4):* a PII policy per tenant (ADR-008); stores per tenant by tier
  (ADR-002); cached answers under the tenant's prefix with a seven-day expiry (ADR-006); credentials kept
  as hashes and no question text in telemetry ([`docs/security.md`](security.md)).
- *Human oversight and control (A5):* no agent decides anything in the gateway (ADR-012); an operator
  holding `Platform.Admin` onboards each tenant; a deploy waits for the `production` environment's
  reviewer and rolls back a failed rollout (`.github/workflows/deploy.yml`).
- *Communication to stakeholders (T2):* what each plan isolates and shares (ADR-002), what the estate
  cannot promise ([`docs/operations/slo.md`](operations/slo.md)) and every benchmark with its misses (`docs/benchmarks/`).
- *Disclosure of AI interaction (T3):* owned by the tenant's application: the gateway relays the RAG
  platform's events unchanged, marks a replayed answer with `x-cache: hit` and adds no notice of its own.
- *Quality of service (F1):* this layer sees tenants, not the people or groups behind them, so it keeps
  service even between tenants: one request path and one added-latency objective for every plan, quotas
  and limits per namespace (ADR-002, ADR-003), measured under a flood (`docs/benchmarks/`).
- *Failures and remediations (RS2):* the degradation modes in their designed order, the error budget
  policy and the regional-outage runbook ([`docs/operations/slo.md`](operations/slo.md)); the failures of section 3.
- *Ongoing monitoring, feedback and evaluation (RS3):* alerts on gateway 5xx, quota rejections, dead
  letters and node pressure (`infra/modules/alerts.bicep`); `tenant_id` on every span; the k6 scenarios
  of `load.yml`; the monthly chargeback reconciling metering with billed cost (`chargeback.yml`).

## 3. Adverse impact

**Limitations.**

- It does not evaluate, filter or moderate questions or answers: groundedness, intelligibility and
  harmful output are handled by the RAG platform. An answer is relayed unchanged and, unless
  it is a refusal, cached for that tenant for up to seven days (ADR-006, `app/main.py`).
- A cache hit needs a cosine similarity of 0.92 or more, so a close but different question receives the
  cached answer; the published load runs use unique questions, so none measures wrong hits.
- PII detection runs in English, on the question only, and nothing here measures what it misses (ADR-008).
- Dedicated tenants get their own index, but the RAG platform does not honour the field yet: every
  tenant is answered from the shared corpus of public regulation ([`docs/security.md`](security.md)).
- The p95 latency is 534 to 564 ms against a 500 ms objective, and the added-latency objective is still to be verified
  (`docs/benchmarks/`); 99.9 % for Enterprise cannot be promised from one node ([`docs/operations/slo.md`](operations/slo.md)).

**Predictable failures.**

| Failure | What the layer does | Stated in |
|---|---|---|
| the RAG platform is down or slow | the circuit opens after five consecutive failures: 503 with `Retry-After`, the reservation released, cache hits still served | ADR-007 |
| Managed Redis is unreachable | fails closed with 503: no reservation, no answer | ADR-003 |
| redaction fails for a tenant that requires it | 502, and nothing is sent or stored | ADR-008 |
| the embedding call fails or is rate limited | the cache behaves as a miss and nothing is remembered | [`docs/operations/slo.md`](operations/slo.md) |
| a flood on the shared Starter gateway | 429 at the door; a known issue under a sustained flood: the Redis pool of that pod is exhausted and its Starter tenants get 500 until it recovers | [`docs/benchmarks/2026-10-25-load.md`](benchmarks/2026-10-25-load.md) |
| node pressure or a spot eviction | Starter pods yield first, then Business | ADR-009 |

**Misuse.**

- A tenant floods the gateway to degrade the others or to obtain work beyond its plan: refused before any
  upstream call (`tests/test_gateway.py::test_tenant_at_its_ceiling_is_rejected_before_any_upstream_call`).
- A tenant sends its token to another tenant's prefix or to the admin API: refused with 403, and the
  admin API is not on the public listener ([`docs/security.md`](security.md)).
- A tenant chooses the policy `none` and sends personal data: the question reaches the platform and the
  cache as written, which is the tenant's choice by design (ADR-008).

## 4. Data requirements

| Data | Where it lives | Rule |
|---|---|---|
| questions and answers | in transit; the tenant's cache | the question sent and kept as written or redacted, by the tenant's PII policy (ADR-008); the answer's events proxied unchanged (ADR-007); both cached under the tenant's prefix for seven days (ADR-006); never written to telemetry |
| usage events | Redis stream, Service Bus, Cosmos DB | tenant, trace id, cost, `cached`, `redacted` and upstream time, no text (`app/main.py`); a stream bounded at 100,000 entries (ADR-004); kept by tier in a partition, a container or a database (ADR-002) |
| tenant records and credentials | Cosmos DB, Key Vault | the SHA-256 hash of each credential, never the credential; the signing key never leaves the vault ([`docs/security.md`](security.md)) |
| the corpus | the shared search index | public regulation, no customer data ([`docs/security.md`](security.md)) |
| test and load data | `tests/`, `load/` | invented companies only; load runs use an in-cluster stub and the policy `none` ([`docs/benchmarks/README.md`](benchmarks/README.md)) |

Not provided: no command in this repository deletes a tenant's data on request, and no expiry is set on
tenant or billing documents; only cached answers expire.

## 5. Summary of impact

| Harm | Mitigation | Evidence |
|---|---|---|
| one tenant reads another's record, credential, quota, cached answer, usage or index | the tenant comes from the token claim only; keys, cache index and stores are per tenant | `tests/test_isolation.py::test_tenant_a_cannot_reach_tenant_b`; ADR-002, ADR-006 |
| one tenant's traffic degrades another's | quota before work; `ResourceQuota`, HPA bounds and `PriorityClass` per namespace | [`docs/benchmarks/2026-10-25-load.md`](benchmarks/2026-10-25-load.md): 181 of 181 protected requests answered, p95 524 ms with enforcement and 596 ms without, one run each |
| personal data leaves the tenant's boundary or is kept against its policy | the per-tenant PII policy; a failed redaction ends in 502 | `tests/test_gateway.py::test_pii_policy_decides_what_leaves_and_what_is_kept`; ADR-008 |
| a tenant is billed for use it did not make | the resolved tenant on every usage event; the cost read from the platform's final event; duplicates bounded under 2 % of a month; reconciliation with billed cost | `tests/test_metering.py::test_metering_accuracy_over_a_simulated_month`; `tests/test_operations.py::test_chargeback_reconciles_both_sources_and_explains_the_gap`; ADR-004 |
| work is done beyond a plan, or without a quota decision | the reservation precedes every call that costs money; fail closed without Redis | `tests/test_gateway.py::test_tenant_at_its_ceiling_is_rejected_before_any_upstream_call`; ADR-003 |
| a credential leaks | hashes at rest, sealed delivery, a signing key that never leaves the vault, no client secret | `tests/test_operations.py::test_business_onboarding_creates_every_artefact`; [`docs/security.md`](security.md) |
| a stale or mismatched cached answer | a similarity threshold of 0.92, a seven-day expiry, `x-cache: hit`, refusals never cached | ADR-006, `app/cache.py`, `app/main.py`; wrong hits are not measured |
| a wrong or harmful answer reaches a reader | none at this layer: the stream is relayed unchanged, and the RAG platform verifies its own answers | ADR-007, ADR-012; an accepted limitation |
