# ADR-001 · AKS here, when Container Apps was the right choice in the other three repositories

**Status:** accepted · **Date:** 2026-10-21

## Context

The three other repositories of this portfolio run on Azure Container Apps and each one records why it
rejected Kubernetes: one team, one tenant, one workload per environment, and a managed platform that
scales to zero removes a control plane nobody needs to operate. Those rejections are only credible if
there is a case where the reasoning reverses. This repository is that case: a hundred paying customers
on one platform, three plans that promise three levels of isolation, and a buyer whose question is
whether one customer can degrade or reach another.

## Options

1. **Container Apps, one environment, one app per tenant.** Simple to deploy; no quotas between apps,
   no network policy between apps in the same environment, no scheduling classes, no node choice.
2. **Container Apps, one environment per tenant.** Isolation at the environment level, but an
   environment is a billed and managed unit, workload profiles are per environment, and a hundred of
   them is a hundred deployments with no shared ingress and no shared cache of routing.
3. **AKS with namespaces per tier and per tenant.** The platform carries the primitives multi-tenancy
   needs and nothing it does not: quotas, limits, network policy, priority classes, taints and
   affinity, workload identity per service account, autoscaling that includes KEDA, and a Gateway API
   ingress that routes per tenant.

## Decision

Option 3. The capabilities that decide it, each one used by a committed manifest or test:

| Capability | Where it is used | Container Apps equivalent |
|---|---|---|
| `ResourceQuota` and `LimitRange` per namespace, differing per tier | `k8s/overlays/*` | none; limits are per replica, never per customer |
| `NetworkPolicy` default deny between tenant namespaces, ingress only from the Gateway | `k8s/base/networkpolicy.yaml` | none between apps in one environment |
| `PriorityClass` with preemption so Enterprise survives pressure and Starter is evicted first | `k8s/base/priorityclass.yaml` | none |
| Node pools with taints, tolerations and affinity: spot for shared tiers, a dedicated pool for Enterprise | `infra/modules/cluster.bicep`, `k8s/overlays/enterprise` | workload profiles exist, but without taints, priority or per-app scheduling rules |
| Workload identity per service account, one federated subject per tenant namespace | `infra/modules/federation.bicep`, `ops/onboard.py` | one identity per app, no namespace |
| HPA on the gateway plus a KEDA `ScaledObject` on Service Bus for the onboarding workers | `k8s/base/hpa.yaml`, `k8s/platform/keda-scaledobject.yaml` | KEDA scaling rules exist; this one is not the differentiator |
| Gateway API with an `HTTPRoute` per tenant namespace | `k8s/platform/gateway.yaml`, `k8s/base/httproute.yaml` | one ingress per app, no shared routing policy |

Multi-tenancy is where those primitives stop being overhead and become the product: the three tiers in
ADR-002 are expressed entirely in them, and `tests/test_isolation.py` and `docs/benchmarks/` exercise
what they promise.

## Trade-offs

- AKS bills continuously. At rest the cluster is one system node, about 185 € a month with the shared
  services around it (`docs/cost.md`); the other repositories cost nothing at rest. `az aks stop` and
  `make destroy` are part of the documented workflow.
- A control plane to operate: version upgrades, add-on lifecycles (the managed NGINX ingress is being
  retired, which is why the Gateway API implementation is used), node image updates. The cluster runs
  the `patch` upgrade channel and node image auto-upgrade to keep that to a minimum.
- Free control-plane tier: no financially backed uptime SLA. The production posture for an Enterprise
  tier is the Standard tier with availability zones (`docs/operations/slo.md`).

## Revisit when

The tenancy model collapses to a single tier, or Container Apps gains per-app quotas, inter-app network
policy and scheduling priority. Either removes the reason this repository runs Kubernetes.
