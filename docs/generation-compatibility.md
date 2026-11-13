> Scope: this document describes the mock-only rehearsal and its PLACEHOLDER
> reservation policy. A separate prepared adapter now exists in
> `generation_probe_live.py`; it uses explicitly user-reported rates and a stricter
> quality gate. See [bounded live-adapter preparation](generation-live-adapter.md).
> Its existence does not authorize execution or establish provider compatibility.

# Step 2 reservation correction (offline only)

This rule replaces the former context-floor reservation lifecycle.
For a count bound to the exact serialized generation request:
`(count + overhead_margin) * input_price + max_output * output_price`.
The explicit example margin is 1,024 tokens: an assumption, NOT a guaranteed bound.
Output is capped at 512 total tokens including reasoning/thinking, not a separate
additional reasoning bucket. Example prices are PLACEHOLDER, not verified account
rates: OpenAI input/output 500/750 nanodollars per token; Anthropic 1000/2500.
Context input allowance is used ONLY for uncounted fallback calculations. The
count-required mock path does not silently fall back after a bad/missing count.
No up-front context holds are allocated. Counted holds are admitted atomically
against carried exposure and the existing arm/overall envelope. Uncertain requests
retain their reservation (or higher observed estimate); unattempted requests hold 0.
Old probe v1 authorities are rejected, not migrated; fresh v2 source/policy is required.
Native usage retains its legacy estimate separately. New prospective settlement
uses observed total input/output counters with the same PLACEHOLDER prices as
the reservation, without an added input margin or extra reasoning output bucket.
A prospective estimate exceeding the counted reservation stops the run.

Corrected local historical inventory is $0.0086193 estimated known usage plus
$0.001239 unresolved=$0.0098583, including previously omitted Phase 2 usage
$0.0011843. Account completeness and actual billing remain unverified.
No live calls, adapter activation or step 3 is authorized.

## Scope

# Generation compatibility and billing safeguards: offline preparation

This slice is an **offline mock rehearsal**, not an authenticated generation
result. It adds no live adapter, key input, CLI activation, payment execution or
automatic enrollment. Existing successful model-access/count measurements remain
historical evidence. They are not guaranteed billable input or cost bounds.

## Uncounted fallback only

The context allowances remain 1,050,000 tokens for Luna and 1,000,000 for Haiku.
With the PLACEHOLDER prices and 512 output tokens, uncounted fallback examples are
$0.525384 and $1.001280. They do not apply as floors to counted requests and are
not provider guarantees. Historical records are never repriced or migrated.
Changed source invalidates old enrollment review and preflight lineage.

## Existing budget, not a new allowance

`generation_probe.create_mock_authority` requires an explicit authorization ID
and nonempty prior inventory. Each inventory entry has its provider, absolute
source-file path, SHA-256, known estimated cost and unresolved exposure. Records
are hash-checked before preparation and execution. These are operator-supplied
amounts, not amounts derived or certified by the hash. Inventory completeness and
account billing remain explicitly unverified.

Local evidence distinguishes campaign estimate $0.0066626, pilot $0.0007724,
Phase 2 smoke $0.0004196 and acceptance $0.0007647, and unresolved original $0.001239.
Their provisional carry is $0.0098583, not a verified invoice or complete account
inventory. All locally identified usage belongs to OpenAI; no arm transfers occur.

The shared authority creates its sole probe allocation identity, not an upfront
context-sized money hold. Changing the run directory or reopening that authority
cannot create another allowance. After an exact-request count, the reservation
and consumption of that count occur in one transaction, checking existing prior
exposure plus observed spend/uncertain holds against $1.50 per arm and $3 overall.
Complete usage may settle a hold; uncertainty retains max(reservation, observed
estimate). Unattempted requests have no reservation and cannot bypass the gate
later. No reconciliation, replacement, refund or reset operation is supplied.

The trusted host must keep using that same authority path and identity. This is
not protection against a malicious operator creating a second authority,
cloning/restoring files, or spending from another machine/application. There is
no account-wide vendor enforcement in the local journal.

## Minimal proposed authenticated probe, not yet implemented or authorized

The offline rehearsal uses a fixed synthetic initial user message and the existing
shared buyer instructions, four tool schemas and final JSON schema. No real user
input, account identifiers or historical response body is included. Order:

1. OpenAI full-request count at `/v1/responses/input_tokens`.
2. One `gpt-6-luna` generation at `/v1/responses`, reasoning `none`, tier `default`,
   output cap 512, no streaming/store/truncation/parallel calls.
3. Anthropic full-request count at `/v1/messages/count_tokens`.
4. One `claude-haiku-5-5` generation at `/v1/messages`, thinking disabled,
   medium effort, `standard_only`/`global`, output cap 512, no parallel tools.

Counts are bound to the exact evolving generation body and consumed once. Both
responses use existing strict model, tier, native usage, cache-regime and schema
validation. Tool-call results are inspected but **never executed**; there is no
continuation, second generation, retry, fallback or full comparison launch.
Missing usage, malformed response, over-reservation usage, wrong model/tier,
transport error or cancellation stops the whole run and retains uncertainty.
A 30-second timeout remains cooperative. Original caller failure is separate from
any later snapshot; a later complete snapshot cannot overwrite a failed caller.

The implementation accepts only exact `httpx2.MockTransport` instances. Its
trusted Python callbacks are not a sandbox. Tests must run under verified OS
network isolation, including subprocesses. The real 48-episode comparison runner
is unchanged and is **not** a shortcut to a two-generation probe.

## Required before any authenticated generation

- Explicit authorization for these two chargeable generations and their scope.
- Complete prior inventory under the original $3 envelope, with no omitted or
  duplicated known estimates or unresolved exposure.
- Review of actual provider accounts, organization/project/workspace identity,
  applicable rates, tax/credit treatment and billing scope.
- Explicit acceptance of conditional context/rate assumptions and residual risk.
- Review provider-side spend limits and their scope, reset period, enforcement
  and possible overshoot. Local estimates are not those controls.
- A separately implemented/reviewed live adapter and private one-shot launcher,
  current-source preflight lineage, frozen source/runtime/rates and new records.

Public [OpenAI spend-limit documentation](https://developers.openai.com/api/docs/guides/spend-limits)
and [Anthropic rate/spend-limit documentation](https://platform.claude.com/docs/en/api/rate-limits)
do not establish which controls are enabled on these accounts. No account dashboard
or billing API has been inspected, and this work changes no account settings.

Even a future successful two-request probe would establish compatibility only for
those tested initial requests, not continuation/history, quality, production
reliability, actual invoices or an absolute cost ceiling. Further comparison work
requires its own reviewed scope within the remaining original allowance.
