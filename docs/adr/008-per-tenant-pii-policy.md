# ADR-008 · PII redaction is a per-tenant policy, not a global switch

**Status:** accepted · **Date:** 2026-10-21

## Context

Questions about regulation carry names, case numbers and other personal data. Some customers are
bound to strip personal data before it leaves their trust boundary; others are bound by their own
retention rules to keep what their staff wrote and are forbidden to alter it. A platform that
redacts for everybody breaks the second group's obligations; one that redacts for nobody breaks
the first group's. A global switch therefore cannot be correct for a hundred customers.

## Options

1. **A global switch**, on or off for the platform.
2. **A per-tenant policy** chosen at onboarding, with three values that map to the obligations
   above.
3. **A per-request flag** sent by the client. Convenient and wrong: the obligation belongs to the
   customer's organisation, not to whoever holds the credential today.

## Decision

Option 2. `Tenant.pii` is one of:

| Policy | What the RAG platform receives | What the cache and the logs may keep |
|---|---|---|
| `none` | the question as written | the question as written |
| `before_upstream` | the redacted question | the redacted question |
| `before_storage` | the question as written | the redacted question |

`app/privacy.py` applies the policy in `Privacy.prepare()`, which returns both texts, and
`app/main.py` uses the first for the upstream call and the second as the cache key and cached
question. Redaction is Azure Language's PII detection on the shared Foundry account, called with
the gateway's identity under the role Cognitive Services User, within the service's documented
limits — 5,120 characters per document, five documents per synchronous request — which the
`Redactor` enforces by chunking at a whitespace boundary and batching, both tested. The
specification's "batches under 1,000 documents" is the language-detection limit; PII detection
allows five, and the code uses five.

A failed redaction for a tenant whose policy requires it is an error, not a fallback: the
request ends with 502 and nothing is sent or stored. Serving the unredacted question would be the
breach the policy exists to prevent.

The usage event carries `redacted`, so the bill shows how many of a tenant's requests paid for
Language text records; `docs/cost.md` prices it per plan.

## Trade-offs

- `before_upstream` changes the question the platform answers; a redacted name can lose the
  context a citation needs. That is the customer's chosen trade, and the policy is theirs to change.
- Every redacted request costs a Language text record per thousand characters on top of the
  upstream answer; the load tenants run with `none` so the benchmarks do not bill Language.
- The language is fixed to English for detection; a multilingual corpus would pass the tenant's
  language with the policy.

## Revisit when

A customer needs a category-level policy (redact persons but keep organisations), which Azure
Language supports through `categories_filter` and this model would carry as a list on the policy;
or a customer needs redaction on the answer path as well as the question path.
