# Bounded generation adapter: prepared, not executed

`generation_probe_live.py` is a separately gated minimal adapter, not the full
comparison campaign. Its development and validation use synthetic keys and mock
HTTP under OS network isolation. No real credentials, authenticated provider
requests, account inspections or payments were used. No CLI or automatic call
site was added. Implementation and documentation approval are not execution approval.

## Scope and durable admission

At most four POST attempts: OpenAI count, one OpenAI generation, then only after
the strict quality gate Anthropic count and one Anthropic generation. The shared
request builders and fixed synthetic transcript from the review packet are
unchanged. Both request 512 total output tokens, including reasoning/thinking,
with reasoning `none` and thinking disabled. The four tool schemas test protocol
compatibility only: no tool execution, follow-up turns, payments, retries, resume,
fallback model/provider or model-read GET is allowed.

`ProviderWire` supplies first-party endpoints/TLS, no proxies/redirects/retries,
authentication only after a durable claim, and credential-reflection checks before
response retention. The exchange bounds requests and raw responses, and bounds
cleanup. The new authority permits only the exact pending count/generation POST
for its current owner, slot, provider and body hash. Claims are unique and retained.
The trusted Python host is not a sandbox against malicious imports, state cloning
or spending by other processes/machines.

Fresh live schema 3 is separate from mock schema 2; old authorities are not migrated
or reset. Use one agreed authority path/identity for the existing $3 aggregate and
$1.50-per-provider envelope. Initialization is exclusive and allocation one-shot.
Minimum documented prior carry is $0.0098583, ALREADY including the unresolved
$0.001239. Do not add that hold twice. Inventory completeness is an explicit
operator attestation, not independently established by a source-file hash.

## Explicit reported prices and conditional reservations

Both providers' standard short rates are **user-reported, not independently
account-verified**: input $0.10/M and output $0.50/M, or 100/500 nanodollars per
token. The live policy does not fall back to the mock's PLACEHOLDER prices.

`reservation = (fresh exact-request input count + 1024) * $0.10/M + 512 * $0.50/M`

The 1,024-token margin is an assumption, not a guaranteed bound. Output is one
total bucket, not an additional reasoning allowance. Count plus margin must be
at most 272,000 OpenAI or 100,000 Anthropic input tokens; otherwise stop before
generation rather than apply unsupported long-context prices. The live path
requires a valid fresh count and never admits a generation through an uncounted
fallback. Context fallback calculations remain confined to the separate estimator.

No cache configuration is requested. Applicable account cache prices are not
verified or admitted by this policy. Nonzero cache categories, unknown categories,
unexpected reasoning/thinking or unsupported tier/geography stop the run and do
not settle as ordinary input. Known ordinary-input/output subtotals are retained;
unknown cache prices are NOT treated as zero. Retain at least the reservation or
a greater known subtotal, with incomplete cost evidence explicit. The report's
`unknown_cost_categories` flag conservatively marks any incomplete request, not
only a proven cache-pricing problem.

Detecting unexpected cache usage after a response cannot prevent its billing.
Execution requires accepting the uncached-mode assumption and residual uncertainty
or first resolving applicable pricing/control. Public cache-rate context does not
establish account applicability and is not silently adopted by this adapter.

Complete accepted uncached short-mode usage settles at the SAME reported rates,
using provider-reported ordinary input/output totals. The margin is not charged
as actual usage; reasoning output is not added twice. Legacy native-price estimates
remain separately labeled and do not release this policy's hold. Model/tier and
counter compatibility do not verify account pricing, invoice deductions or limits.

Historical counts 386/1,121 measured a different request body. Illustrative
reservations at the reported rates are $0.000397 and $0.0004705, totaling $0.0008675;
with prior carry the example commitment is $0.0107258. Fresh counts must determine
actual prospective reservations. At allowed short-count thresholds, conditional
reservations are at most $0.027456 and $0.010256, totaling $0.037712. These are local
arithmetic limits under the stated assumptions, NOT guaranteed provider cost caps.
Existing arm and aggregate limits are checked atomically with carried exposure.

Account facts are reports only: OpenAI $10 enforced project limit and $119.99
credits; Anthropic $10 workspace limit and $99.99 promotional credits; auto-reload
off for both. They were not independently verified and neither expand the $3
authorization nor prove absence of overshoot, fees, expiration or other spending.

## Strict quality and interruption behavior

Only valid final structured output or one supported tool call with validated
arguments passes the quality gate after accepted accounting and cleanup. Tools
never execute. Refusal, content filtering, truncation, invalid final output or
arguments, and unavailable tools stop before the next provider. Complete accepted
usage remains settled; it is not rolled back because useful output failed.
Protocol/accounting completion is distinct from useful-output success.

Missing/invalid usage, identity/mode/price uncertainty, output above 512, mismatched
counts/bodies, insufficient budget, transport failure and cleanup failure stop
without retry. Interruption before observation retains pending exposure; failure
AFTER successful observation preserves completed accounting. Original caller
failure stays separate from a later durable-state snapshot. A stopped or finished
authority cannot restart. No automatic reconciliation or recovery is added.

## Separate future execution boundary

A live authority requires a frozen source hash and exact generation-body hashes,
the shared authorization ID, a hash-bound prior inventory and completeness
attestation, reviewed preflight lineage, accepted reported rates/margin/uncached
risks, strict quality gate, account-report provenance, and separate explicit
execution authorization. These are trusted operator inputs, not cryptographic
proof of human consent. Development initializes only synthetic test authorities.

A later execution request must identify the two providers, one count and one
generation per provider, the 512-total-output cap, no tools/retries, the existing
shared/arm envelope and prior exposure, reported rates, assumed margin and residual
unpriced-category risk. Fresh source/body review, inventory reconciliation and
preflight-lineage acceptance remain necessary. No live execution is approved by
this preparation, documentation, testing or commit workflow.

## Offline verification

On the unchanged adapter code, 61 focused tests passed and full validation passed
1,449 tests plus dependency, lint/format, repeated demos, evaluator and isolated
viewer-server checks. Synthetic wire tests exercise request fencing, quality stops,
usage/category uncertainty, interruptions, retained exposure, credential reflection,
review bindings and restart denial. No authenticated compatibility or billing
verification is implied; browser rendering was not tested by the validator.
