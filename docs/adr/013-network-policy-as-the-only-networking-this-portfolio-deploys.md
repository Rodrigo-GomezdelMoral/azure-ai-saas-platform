# ADR-013 · Cluster-level network policy, the only networking this portfolio deploys

**Status:** accepted · **Date:** 2026-10-21

## Context

The landing zone's ADR-002 records why this portfolio deploys no spoke network, no private endpoint and
no private DNS zone: identity is the security boundary, and the private networking would add cost and
operations for no capability the workloads need. Every repository repeats that in one sentence and does
not re-argue it. This repository is the one place where networking solves a problem identity cannot:
two pods in the same cluster can reach each other over the pod network regardless of what token either
of them holds.

## Options

1. **No network policy.** Namespaces are an organisational boundary only; a compromised Starter gateway
   can call a Business gateway's pod directly.
2. **A service mesh with mutual TLS and authorisation policies.** Strong, and a second control plane
   with its own certificates, sidecars and failure modes; the Gateway API implementation already runs the
   mesh's control plane for ingress alone.
3. **Default-deny `NetworkPolicy` per namespace, enforced by the Cilium data plane.** East-west traffic
   is denied unless a policy allows it; the allowed paths are three and written down.

## Decision

Option 3. Every tenant namespace carries (`k8s/base/networkpolicy.yaml`):

- `default-deny`: an empty `podSelector` with both policy types, which denies all ingress and all egress
  for every pod in the namespace;
- `allow-ingress-from-platform-gateway`: port 8000 from the `ingress` namespace only, where the platform's
  Gateway proxies run;
- `allow-egress-to-azure`: DNS in `kube-system`, TCP 443, 10000 and 5671 (AMQP, which the admin route
  uses to queue an onboarding order) to public addresses with the private ranges excluded (Entra, Cosmos,
  Service Bus, Event Grid, Foundry, Search, Key Vault, the RAG platform and Managed Redis), and port 8080
  to the load-test stub in `platform`.

The platform namespace mirrors it for the workers (`k8s/platform/networkpolicy.yaml`). Cross-namespace
traffic between tenants is therefore denied rather than unconfigured, which is the shape the audit asks
for, and the cluster's network plugin is Azure CNI overlay with the Cilium data plane and Cilium policy
(`infra/modules/cluster.bicep`), so the policies are enforced in eBPF rather than iptables.

The `ingress` namespace holds nothing but the Gateway and the proxies AKS deploys for it. It enforces Pod
Security `baseline` and warns and audits `restricted`, because the managed proxy sets no seccomp profile
and the customisations AKS allows on it cannot add one; every pod this repository deploys meets
`restricted`, which the `platform` and tenant namespaces enforce.

Outside the cluster there is still no network of this repository's own: the cluster lives in its managed
virtual network, egress leaves through the standard load balancer, and every Azure service is reached at
its public endpoint with an Entra token, as the landing zone decided.

## Trade-offs

- Policies are allow-lists of CIDRs for Azure endpoints rather than service tags; Kubernetes network
  policy has no notion of Azure service tags. The private ranges are excluded so that a tenant pod can
  never reach another pod or the node network through the egress rule.
- Every new dependency is a policy change. That is the point.

## Revisit when

Mutual TLS between tenant gateways and the platform becomes a contractual requirement, at which point
the mesh that already runs for ingress is extended to east-west with authorisation policies; or the
landing zone deploys private endpoints, at which point the egress rules narrow to the virtual network.
