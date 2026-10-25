# Onboarding times, 2026-10-24

The first measured onboardings, run from a workstation with `python -m ops.onboard` against the
deployed estate (commit `e9905be` plus the close-out changes, gateway image `e9905be-closeout-6`). The
times are the command's own per-step output; the credential line is omitted. The console path, through
the admin API, Service Bus and the KEDA-scaled worker, was not timed; `load/onboarding.js` measures it
for ten tenants at once.

| Tenant | Plan | Run | Total |
|---|---|---|---|
| `load-starter` | Starter | first, cold clients | 14.1 s |
| `load-starter` | Starter | second, the same tenant (every step converges) | 8.8 s |
| `load-starter` | Starter | third, after the quota keys moved to a hash tag | 9.5 s |
| `load-business` | Business | first | 63.3 s |

Business, step by step:

```
register    14.9 s  app registration <id> holds the app role tenant:load-business
namespace   14.9 s  15 objects applied for t-load-business
federate     3.1 s  id-saasgw trusts system:serviceaccount:t-load-business:gateway
index       11.7 s  index t-load-business created and filling from the shared corpus
store        9.8 s  saas/usage-load-business with hierarchical partition keys
bucket       3.4 s  500 cents of burst, 120 a minute, index idx:cache:load-business
credential   2.7 s  credential issued, valid until 2027-10-24
notify       1.9 s  tenant.onboarded published to the platform topic
load-business (business) ready in 63.3 s
```

Starter, second run:

```
register     0.0 s  authenticates with the platform credential; no app registration
namespace    0.0 s  shared namespace tenants-starter
federate     0.0 s  covered by the shared gateway's federation
index        0.0 s  shared index regulations, scoped by the gateway
store        0.0 s  partition in the shared container saas/usage
bucket       3.4 s  50 cents of burst, 20 a minute, index idx:cache:load-starter
credential   2.3 s  credential issued, valid until 2027-10-24
notify       1.9 s  tenant.onboarded published to the platform topic
load-starter (starter) ready in 8.8 s
```

What the two tenants then proved through the starter and dedicated gateways, reached with
`kubectl port-forward` while the public Gateway was not yet programmed:

- `GET /v1/usage` with each credential named its own tenant and plan; the Starter credential sent to the
  Business tenant's gateway was refused with 403, "this gateway does not serve that tenant".
- A question through the starter gateway streamed the platform's five phases in 43 s; the same question
  again was a cache hit (`x-cache: hit`) in about a second.
- A paid answer of 0.002186 € raised the tenant's month by 0.2186 cents, and the Function's billing
  document in Cosmos showed the same amount with its request and cache counters; one request through
  the in-cluster stub added exactly one cent.

Configuration: one `Standard_D4as_v5` system node, Kubernetes 1.36.4, Managed Redis `Balanced_B0`,
Cosmos DB serverless, Sweden Central; the workstation in Spain.
