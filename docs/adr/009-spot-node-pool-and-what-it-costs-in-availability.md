# ADR-009 · A spot node pool for the shared tiers, and what it costs in availability

**Status:** accepted · **Date:** 2026-10-21

## Context

The cluster is the one resource in this portfolio that bills by the hour whether or not a customer is
asking anything. The system pool is one node at rest; the question is what the user capacity under load
should run on, and whether every tier should run on the same answer.

## Options

1. **Regular nodes only.** Predictable, no evictions, full price for capacity that exists only under load.
2. **Spot nodes only.** The cheapest capacity, with evictions on thirty seconds' notice; no tier can be
   promised continuity.
3. **Spot for the shared tiers, a regular dedicated pool for Enterprise.** The price of eviction is paid
   by the tiers that are priced for it, and the tier that promises continuity never sees a spot node.

## Decision

Option 3 (`infra/modules/cluster.bicep`):

| Pool | Size | Priority | Scale | Who schedules there |
|---|---|---|---|---|
| `system` | `Standard_D4as_v5` | regular | 1 | the control-plane add-ons and the Gateway proxies; Starter and Business pods at rest |
| `spot` | `Standard_D2as_v5` | spot, `Delete` on eviction, no price cap | 0–2 | Starter and Business gateways under load, by **preferred** affinity |
| `enterprise` | `Standard_D2as_v5` | regular | 0–1 | Enterprise gateways only, by taint `tier=enterprise:NoSchedule` and **required** affinity |

The affinity to spot is preferred, never required: when the spot pool has no capacity or is being
evicted, Starter and Business pods schedule on the system node. Priority classes decide who yields when
the system node is full: Starter first, then Business, never Enterprise (ADR-002).

**What it costs in availability.** A spot eviction removes a node with thirty seconds' notice. Business
runs two replicas with a pod disruption window set by the HPA minimum, so an eviction costs capacity, not
service; Starter runs one replica at rest and an eviction costs the seconds a reschedule takes, which is
inside the Starter error budget (`docs/operations/slo.md`). Enterprise is not exposed.

**What it saves.** A spot `D2as_v5` costs about a fifth of a regular one at today's retail price in
Sweden Central (`docs/cost.md`): two spot nodes under load cost less than half of one regular node.

## Trade-offs

- Spot capacity is not guaranteed to exist; the autoscaler may find none. The fallback is the system node
  and, if the system node is full, the Starter tier degrades first by design.
- Evictions appear as pod restarts in the tenant namespaces; the dashboards in `docs/operations/slo.md`
  distinguish them from crashes by the node event.

## Revisit when

The Business tier is sold with an availability objective that spot cannot meet, in which case Business
moves to the dedicated pool and the pool's ceiling rises; or the spot eviction rate observed in a month
exceeds the Starter error budget.
