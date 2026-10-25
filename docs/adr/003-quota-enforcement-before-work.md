# ADR-003 · Quota enforcement before work, in euro cents

**Status:** accepted · **Date:** 2026-10-21

## Context

Every answer costs money upstream: the RAG platform spends model tokens and reports the result as
`cost_eur` in its final `verify` event. A plan is a monthly amount of that money plus a rate. The
platform must refuse a request that would exceed the plan *before* paying for it, because
discovering the overrun after inference means the platform paid for an answer it cannot bill.

## Options

1. **Meter after the fact.** Count usage from the final event and suspend the tenant when the
   month's total passes the plan. Simple, and the last requests of every month are free.
2. **Rate limits only.** Requests per minute at the edge. Bounds load, not money: a tenant within
   its rate can still spend ten times its plan on long answers.
3. **A token bucket in money, checked on entry, reconciled on exit.** Reserve the estimated cost
   before the upstream call, charge the measured cost after it, refund the difference, release
   the reservation when the request fails.
4. **Azure API Management** in front of the gateway with its quota policies. It counts calls and
   bytes, not euros, and it is a second gateway to run, pay for and keep consistent.

## Decision

Option 3, in **euro cents**, not tokens. The RAG platform's final event carries `cost_eur` and no
token counts, and that platform is closed; cents are also the unit the margin thesis needs.

`app/quotas.py` keeps three keys per tenant in Redis and one Lua script makes the decision
atomically: a burst bucket (`quota:{<id>}:bucket`, capacity `burst_cents`, refilled at
`monthly_cents` per month), a per-minute counter (`quota:{<id>}:rpm:<minute>`) and the month's
measured spend (`quota:{<id>}:month:<YYYY-MM>`). On entry the gateway reserves the plan's
`estimated_cents`; the script refuses with a reason — `monthly` when measured spend plus the
reservation would pass the plan, `burst` when the bucket cannot cover it, `rate` when the minute is
full — and a `retry_after`. On exit the gateway settles the reservation with the cost from the
final event, which refunds the unused part of the estimate or charges the excess, and adds the
measured cost to the month. A failed or cancelled request releases its reservation entirely.

The braces are a Redis hash tag. Managed Redis runs clustered, a script may only touch keys that
live in one slot, and the tag puts every key of a tenant in the same slot; without it the first live
request failed with `CROSSSLOT`, which `fakeredis` in the unit tests cannot show. The measured cost
is kept in fractions of a cent (`INCRBYFLOAT`): an answer from the platform costs between a quarter
of a cent and a cent, so rounding to whole cents would have billed most answers at zero. The
reservation, a plan constant, stays a whole number of cents.

A refusal costs nothing: it is one Redis round trip and a 429 with `Retry-After` and a
`problem+json` body that names the reason, so a customer can tell a rate limit from an exhausted
plan. The quota-rejection alert on the gateway's 429 rate is a business signal: tenants hitting
their ceiling are tenants to upsell, and a tenant hitting it every day is a plan that is priced
wrong.

Redis unavailable means the gate cannot decide, and the gateway **fails closed** with 503: the
quota-bypass threat in `docs/security.md` is a tenant obtaining work without a reservation, and an
open gate would be exactly that.

## Trade-offs

- The estimate is a plan constant, not a prediction per request. A long answer is charged at
  settlement, so a tenant can overshoot its burst by one request's difference; the monthly cap is
  measured spend and cannot be overshot by more than one reservation.
- Reservation and settlement are two round trips per request to Redis, about a millisecond each,
  on the request path. The alternative, a database write per request, is what ADR-004 avoids.
- Cents lose the token granularity a finer product might bill. If the RAG platform ever exposes
  token counts in its final event, the unit can change without changing the mechanism.

## Revisit when

Plans are priced per token rather than per euro of cost, or a plan needs more than one bucket
(for example, separate reads and generations), or the gateway runs in more than one region and the
bucket needs to be regional.
