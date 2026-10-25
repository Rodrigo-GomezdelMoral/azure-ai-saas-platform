# Benchmarks

Three k6 scenarios under `load/`, run by `load.yml` from a GitHub-hosted runner against the deployed
gateway, never against the RAG platform. This file records the method; each published run is a dated
file next to it, with the configuration and the SKUs it ran on, and the README links the latest.

**No load result is published yet.** The cluster was first deployed on 2026-10-24; the onboarding times
measured that day are in `2026-10-24-onboarding.md`, and the three k6 scenarios have not run. A load
test without a committed result is a script, not
evidence, and this document does not claim numbers it does not have.

## What is measured, and what is not

- **The gateway, not the platform.** The tenant gateways under test point at `upstream-stub` in the
  `platform` namespace (`load/stub.yaml`): nginx replaying the recorded `/v1/answer` stream of the RAG
  platform with a fixed `cost_eur` of 0.01, one cent, so that the monthly quota bites. The workflow sets
  `UPSTREAM_URL` on the load tenants' deployments for the duration of the run and restores it afterwards.
  Reasons: k6 against the platform would spend model tokens, hit the model deployment's rate limits and
  measure the platform rather than the gateway.
- **The whole gateway path.** Every question is unique, so each request takes the full path: tenant
  resolution, quota reservation, embedding of the question on the shared Foundry account, a cache miss,
  the stub, settlement and metering. The embedding call is the one external dependency in the measured
  path; its deployment's tokens-per-minute limit caps the achievable rate, and a run that hits it shows
  up as a rising p95 with the stub idle.
- **The embedding deployment's own limit.** The shared `text-embedding-3-small` deployment admits 120
  requests every ten seconds for every tenant together. Above twelve a second the gateway answers without
  probing the cache, the degradation `docs/operations/slo.md` lists, so the higher rates measure the
  gateway without its cache lookup.
- **PII off.** The load tenants are onboarded with the policy `none`, because Language bills per text
  record and redaction latency would be measured instead of the gateway's.

## Scenarios

| Script | What it does | Objective | Result file |
|---|---|---|---|
| `load/baseline.js` | five fixed arrival rates, 5, 10, 20, 40 and 80 requests a second, 45 s each, on one Business tenant whose quota enforcement is off for the run, because the result is the gateway's ceiling, not the plan's | p95 of successful requests under 500 ms and failures under 1 % at each rate; the first rate that breaks either is the gateway's measured ceiling | `baseline.json` |
| `load/noisy-neighbour.js` | a Starter tenant floods the shared gateway at 100 requests a second while a Business tenant runs 1 a second in its own namespace, inside its plan's 120 a minute and its 500-cent burst, for three minutes; run twice, with quota enforcement on and off on the Starter gateway | the protected tenant's p95 with and without enforcement, side by side | `noisy-neighbour-on.json`, `noisy-neighbour-off.json` |
| `load/onboarding.js` | ten tenants onboarded at once through the admin API, each virtual user polling until its tenant is ready | time to ready, p95 under two minutes | `onboarding.json` |

The noisy-neighbour comparison is the proof the repository is built around: with enforcement on, the
flood is cut at the Starter plan's twenty requests a minute and its monthly ceiling, at the cost of a
Redis script per request; with enforcement off, the flood reaches the embeddings, the stub, the stream
and the node, and the protected tenant pays for it. The published chart shows both p95 values.

## Set-up for a run

1. Deploy the platform and the starter namespace (`deploy.yml`), and the stub: `kubectl apply -f load/stub.yaml`.
2. Onboard two load tenants with the policy `none`: `make onboard NAME="Load Starter"` and
   `make onboard NAME="Load Business" PLAN=business`. Keep the two credentials as the repository secrets
   `LOAD_STARTER_TOKEN` and `LOAD_BUSINESS_TOKEN`, and `/t/load-business` as the variable
   `LOAD_BUSINESS_PREFIX`.
3. Run `load.yml` with the scenario, or all three. The results come back as the job summary and as an
   artifact; the ones worth publishing are copied here as `YYYY-MM-DD-<scenario>.md` with the SKUs below.
4. Delete the stub when done: `kubectl delete -f load/stub.yaml`.

## Configuration every published run records

- Date, commit, the k6 version and the runner (GitHub-hosted `ubuntu-latest`, so the network path is
  from a Microsoft data centre to Sweden Central).
- Cluster: one `Standard_D4as_v5` system node, spot `Standard_D2as_v5` 0–2 (the subscription's regional
  spot quota of 3 vCPUs admits one spot node, so record how many ran), Enterprise pool 0–1;
  Kubernetes 1.36; Gateway API implementation `approuting-istio` with two proxy replicas.
- Managed Redis `Balanced_B0`; Cosmos DB serverless; the embedding deployment and its tokens-per-minute
  limit on the shared Foundry account.
- The gateway image tag, the plan of each load tenant, and `ENFORCE_QUOTA` for each noisy-neighbour run.
