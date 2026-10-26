# Onboarding a customer

Onboarding turns a company name and a plan into a serving tenant. The same code runs from a workstation
and behind the console; both paths end with the customer's first credential and a tenant document whose
`steps` field is the audit trail of what was created.

## What is created, per plan

| Step | Starter | Business | Enterprise |
|---|---|---|---|
| `register` | nothing: the platform credential is the identity | app registration + `tenant:<id>` role on the gateway | same |
| `namespace` | the shared `tenants-starter` namespace | `t-<id>` from the business overlay | `t-<id>` from the enterprise overlay, dedicated pool |
| `federate` | covered by the shared gateway | federated credential on `id-saasgw` for `t-<id>/gateway` | same |
| `index` | the shared index, scoped by the gateway | dedicated index `t-<id>`, filled by a copy of the shared skillset and indexer | same |
| `store` | a partition in the shared `usage` container | container `usage-<id>` in the shared database | database `t-<id>` with its own `usage` container |
| `bucket` | Redis token bucket at the plan's burst, cache index `idx:cache:<id>` | same | same |
| `credential` | Key-Vault-signed credential, hash in Cosmos, public key in the JWKS document | same | same |
| `notify` | `saas.tenant.onboarded` on the platform's Event Grid topic | same | same |

Measured on 2026-10-24 from a workstation: 9 to 14 s for a Starter tenant and 63 s for a Business
tenant (`docs/benchmarks/2026-10-24-onboarding.md`).

Every step is idempotent: a redelivered order or a second run with the same name converges on the same
tenant. A failed step marks the tenant `failed` with the reason; fix the cause and run again.

## From the shell

```
eval "$(azd env get-values | sed 's/^/export /')"      # the deployment's endpoints and ids
make onboard NAME="Globex Industries" PLAN=business PII=before_storage
```

The command prints one line per step with its duration, the total elapsed time, and the credential once.
The credential is a JWT signed by the vault's key; the platform keeps its SHA-256 hash and nothing else.

## From the console

`/console` and the admin API it calls are served by the starter gateway pod and reached through a port-forward,
never through the public listener: `kubectl -n tenants-starter port-forward deployment/gateway 8000:8000`, then
`http://localhost:8000/console/`, the one redirect URI the gateway's registration accepts. The operator signs in with Entra (the `Platform.Admin` app role on
the gateway's registration), submits the company name, the plan and the PII policy, and watches the
eight steps complete. Before submitting, the browser generates an RSA key pair; the worker seals the
credential to that public key (AES-GCM for the credential, RSA-OAEP for the AES key) and the status route
returns the ciphertext once and removes it. The platform never persists a plaintext credential.

The console's order goes through the admin API to the Service Bus `onboarding` queue; KEDA scales the
onboarding workers from zero on queue length, and the worker runs the same eight steps.

## Prerequisites

1. The infrastructure is deployed and the gateway image is pushed (`make image`).
2. The Microsoft Graph grants that Bicep cannot hold exist: the gateway's app registration with the
   `Platform.Admin` role and the `Console.Access` scope, `id-saasonboard` as an owner of that registration
   holding `Application.ReadWrite.OwnedBy` and `AppRoleAssignment.ReadWrite.All`, and `id-saasgw` holding
   `Rag.Query` on the RAG platform's API. The author's bootstrap script creates all of them.
3. For dedicated plans, the environment carries `GATEWAY_CLIENT_ID`, `GATEWAY_IDENTITY_ID`,
   `COSMOS_ACCOUNT_ID` and `OIDC_ISSUER`, all outputs of the deployment.

## Recording script

Part 5 of the demo guide fixes the shot list; this is the onboarding segment of it, timed from a real run.

| Time | Screen | Action |
|---|---|---|
| 0:12 | Terminal | `make onboard NAME="Globex Industries" PLAN=business`. Let the eight lines and the elapsed time show; the credential line is the last thing on screen |
| 0:30 | Terminal | `kubectl get resourcequota,networkpolicy -A` across `tenants-starter`, `t-globex-industries` and an enterprise namespace |
| alt. | Browser | The console: type the name, pick the plan, press Onboard, watch the steps turn green, reveal the sealed credential |

Neutral terminal context and title bar; no subscription, tenant or object id on screen. The recording's
link belongs near the top of the README and is added once the file exists.
