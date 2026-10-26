# ADR-011 · Rejection: no Front Door at this scale, and the traffic level at which that reverses

**Status:** accepted · **Date:** 2026-10-21

## Context

Customers reach the gateway through one public IP on the cluster's standard load balancer, behind the
Gateway API implementation of the application routing add-on. A global entry point (Azure Front Door)
would put a web application firewall, edge TLS, anycast routing and caching in front of it. The question
is whether a hundred customers in one region need any of that today, and what it costs to add it now
rather than later.

## Options

1. **Front Door Standard now.** A base fee of about 31 € a month at today's retail price plus traffic,
   a WAF policy to maintain, origin health probes, and a second place where routing is configured.
2. **Front Door Premium now.** The managed WAF rule sets and Private Link to the origin; several times
   the base fee, and a private origin this estate does not have (the landing zone's ADR-002).
3. **The load balancer and the Gateway API alone**, with TLS terminated by an HTTPS listener on the
   Gateway and rate limiting done per tenant by the quota gate, which is where a multi-tenant platform needs it.

## Decision

Option 3. The protection a buyer would ask about at this scale, that one tenant cannot flood the
service for another, is the quota gate's job (ADR-003) and is measured in `docs/benchmarks/`; a WAF at
the edge would not know which tenant a request belongs to. The platform runs in one region, its customers
are in Europe, and the Gateway API listener already does routing and can terminate TLS. Front Door would add
a monthly fee and a second routing configuration to maintain for no measured benefit.

**What this estate runs.** The listener is HTTP on port 80 of the public IP: the estate owns no domain, so
there is no certificate to present, and bearer tokens cross the internet unencrypted. That is acceptable for
invented tenants and their load-test credentials, which are reused between runs and valid for a year (revoking
one means writing `revoked` on its credential record; no command does it), and for nothing else. The production step
is an HTTPS listener with a Key Vault certificate and an Azure DNS record, which the application routing
add-on automates (Learn: `aks/app-routing-gateway-api-dns-tls`); the admin console and the admin API never use
the public listener (`docs/onboarding.md`), so no `Platform.Admin` token crosses it.

**Where the decision reverses.** Any one of these, and the first that occurs:

- **Sustained traffic above about 1,500 requests a second.** The Gateway's two proxy replicas on the
  system node are the ingress; above that rate the proxies and the single load balancer path become the
  bottleneck before the gateways do, and an edge that absorbs TLS and short-circuits cached answers is
  worth its fee. The baseline benchmark in `docs/benchmarks/` is what measures the actual ceiling.
- **A second region.** Front Door is the only sensible way to route customers to the nearest healthy
  region; a regional load balancer cannot.
- **A customer outside Europe**, or a contract that names a WAF. Both are Enterprise-tier conversations
  and are priced into that tier when they happen.

## Trade-offs

- No edge WAF today. The gateway rejects unauthenticated requests before touching any store
  (`tests/test_tenancy.py::test_middleware_rejects_before_routing`), but volumetric attacks reach the
  load balancer. Azure's platform DDoS protection covers the public IP at the network layer.
- TLS certificates are the Gateway's to manage rather than the edge's, and this estate has none yet (above).

## Revisit when

The baseline benchmark shows the ingress saturating below the tiers' combined ceilings, or the first
of the three reversal conditions above is met.
