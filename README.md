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

Phase 2 of 9: the working agreement (`CLAUDE.md`), the budget test, typed settings, the domain
model, and the first layer of infrastructure — the three workload identities, the Key Vault with
the one signing key, Application Insights, and the roles each identity holds on the landing zone's
shared services. The cluster, the data plane, the manifests, the gateway, the onboarding recording,
the load-test results and the unit economics arrive with their phases; this file is completed in
phase 9.

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
  `ops/`, `functions/`) and 600 of support Python (`tests/`, `load/`); 500 lines of Bicep; 500 of
  YAML under `k8s/`; 700 of TypeScript; fewer than 25 comment lines. The actual counts are recorded
  here at the end of every phase.
