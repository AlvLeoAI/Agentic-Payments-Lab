# Luna / Haiku paired evaluation — guarded preparation

This is an uncommitted engineering candidate, not a live comparison result.
Ordinary execution and validation use in-memory mocks. The separate
[preflight contract](paired-preflight.md) bounds any explicitly approved model
access/count probes and keeps generation behind a further review. Real payments,
publication and deployment remain outside this slice. The historical Luna campaign,
its immutable enrolled policy, saved results, human-review-pending flags and the
original unresolved USD 0.001239 reservation remain separate and unchanged.
Historical estimates are not repriced. The viewer still shows its original saved
pilot snapshot; it does not execute this harness.

## Matched experiment

The frozen plan contains eight existing semantic cases, three trials and two
providers: 48 episodes. Cases are free purchase, paid purchase, same-purchase
replay, zero budget, unaffordable premium, failed delivery, unknown payment and
an instruction-like synthetic subject. Each provider/trial has fresh, equivalent
semantic simulator fixtures. Only paid/replay share state within that arm/trial.
Provider-specific task/grant/purchase/quote identities are intentionally distinct.
The fixed simulator clock, catalog, budgets, goals and seeded fault behavior agree.
There are 42 fixtures, each with the original four authority/metadata databases.

Provider order alternates for each case; the first provider reverses in trial two.
The whole schedule is frozen before execution. An ordinary known model-quality
failure is retained, never replaced. A replay without a successful paid
prerequisite is explicitly not applicable, with zero model requests; it is not
silently turned into a fresh purchase. Any uncertainty or control/financial
failure stops both arms and leaves remaining slots unattempted.

| Property | OpenAI arm | Anthropic arm |
| --- | --- | --- |
| Exact model | `gpt-6-luna` | `claude-haiku-5-5` |
| Model configuration | reasoning `none`, tier `default` | thinking disabled, effort `medium`, tier `standard_only`, inference `global` |
| Wire contract | Official pinned OpenAI Responses SDK | Native `/v1/messages` JSON over the existing pinned HTTPX transport |
| Output | Strict shared JSON schema, max 512 tokens | Same schema, max 512 tokens |
| Tools | Same four strict client tools, parallel calls disabled | Same tools, `disable_parallel_tool_use: true` |
| Maximum per episode | 6 generations, 8 tool attempts, 30s cooperative timeout | Same |
| Maximum per arm | 144 generation attempts and 144 separately counted input-count attempts | Same |
| Prospective estimate/exposure ceiling | USD 1.50, no transfer | USD 1.50, no transfer |

The aggregate ceilings are 288 generations, 288 separate count attempts and USD
3.00. Full serialized count and generation bodies are independently bounded to
32,768 bytes; generation responses to 65,536 bytes, count responses to 4,096.
No retries, fallbacks, selective replacement, automatic continuation, reset or
resume are implemented. These thinking settings do not imply equal internal work.

Both arms receive the unchanged buyer instructions and the same eligibility-filtered
catalog, including its generic refusal reason. Neither sees hidden global prices
in a refused catalog. Deterministic eligibility selects the cheapest approved
service meeting the goal; the model chooses tool sequence/arguments, compliance,
when to stop and its explanation. It cannot change the bound goal or financial
authority. A shared supported schema intersection omits provider-unsupported
length/pattern constraints from **both** wire schemas; the original local Pydantic
tool/final-output validation remains authoritative.
The current four argument/final schemas contain no enum/const literals affected
by Anthropic's documented capitalization caveat. Capitalized tool names are
unavailable; changed-case service/quote identifiers are never lowercased into
valid authority. Exact local checks reject them without payment, while trustworthy
usage remains accounted. Both arms follow that same acceptance rule.

The separate comparison host loop calls the official `AsyncOpenAI.responses`
interfaces and a narrow native Messages adapter. It does not widen `run_buyer`,
`GuardedLunaModel`, the historical Agent SDK runner or the live x402 fence.
No Anthropic SDK/dependency upgrade is required. The pinned environment remains
OpenAI 3.42.1 and Agents SDK 0.24.0. Both default model paths construct in-memory
HTTP mocks. The separate live module specializes the exchange only after explicit
review and enrollment; no CLI activation exists. Imports neither discover
credentials nor create clients. Shared request construction, limits and local
validation remain identical across transports.

## Accounting and admission

One comparison SQLite journal owns both arms' exclusive owner, next schedule
slot, count witnesses, generation reservations, tool intents/results and episode
results and unique wire permissions. A single `BEGIN IMMEDIATE` checks arm and
aggregate limits and commits
each reservation. A pending count, generation, tool or missing episode result
cannot be reset or resumed on restart. Journal reporting uses one explicit read
snapshot. The financial ledger, simulated payment authority, merchant delivery
store and agent binding/quote store retain their existing responsibilities.

Count each evolving full effective request, including tools, final schema,
instructions, settings and all tool-result history. Bind the exact serialized
count payload hash and canonical generation-body hash to its episode/provider.
One count witness can authorize only one generation. Changed bodies, stale or
missing witnesses are rejected. Canonical JSON comparisons preserve boolean,
integer and floating-point types before persistence or dispatch. The count and
generation attempts have separate durable counters; a count is not reported as
a generation.

Step 2 reserves `(counted input + explicit overhead margin) * input price +
max output * output price`. The example margin is 1,024 tokens, an assumption,
not a guaranteed bound. The output cap is 512 total tokens, including thinking
or reasoning, not an extra bucket. Example prices are **PLACEHOLDER, not verified
account rates**: Luna input/output 500/750 nanodollars per token, Haiku 1000/2500.

A context-window input allowance is used ONLY for an uncounted fallback calculation
(Luna 1,050,000; Haiku 1,000,000). It never overrides a counted reservation with a
context-sized floor. The count-required mock flow stops on missing/bad witnesses;
it does not silently replace them with fallback permission.

Prior estimates and outstanding uncertain holds plus each new counted reservation
must fit the existing arm and total limits. Counting is not blocked merely because
a hypothetical context-sized generation would exceed that budget. Complete usage
settles using observed total input/output at the same frozen PLACEHOLDER rates;
the legacy native usage estimate is retained separately, not used to release the
new-policy hold. Unknown outcomes retain exposure; over-reservation usage stops.
No arm transfers, automatic reset or account-wide spending guarantee is implied.
Historical records and their policies remain unchanged. Account scope/prices,
provider fees and true invoice totals are unverified, and live use remains gated.

Anthropic input is `input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`; the 5m/1h counters allocate creation input and are not
added a second time. Missing, malformed or inconsistent fields remain unknown.
Explicit zero creation permits a labelled derived zero allocation if the TTL
object is absent/null; it does not claim the provider returned those fields.
Nonzero cache counts are retained and priced when valid, but stop the explicitly
uncached Haiku arm. Luna keeps its distinct native usage/TTL parsing. Observed
usage completeness, model/tier/pricing verification, response validity and exposure
resolution are separate report fields.

Missing usage, cancellation or interrupted persistence retains the worst-case
reservation and reports unknown/incomplete totals. This held exposure is the
fail-closed equivalent of worst-case accounting; it is **not fabricated actual
spend** and is never settled to zero. Known partial tokens remain visible. A
known estimate above the reservation records the excess and outstanding exposure
as at least `max(reserved, observed)`; both arms stop. Identical settlement
evidence is an idempotent no-op, including after stop/completion/reopen. Conflicting
replay preserves the original accounting and durably blocks continuation.

Refusals and token-limit truncations retain distinct stop/schema outcomes and
settle trustworthy actual usage when complete. Truncated tool arguments never
execute. OpenAI incomplete reasons are inspected individually. The 512-token
setting is a limit, not proof that 512 tokens were billed. Neither refusal nor
truncation triggers a retry or continuation within the episode.

## Interruption and evidence

| Boundary | Durable evidence / recovery behavior |
| --- | --- |
| Owner/episode committed, before count | Claimed schedule cannot restart |
| Count intent before dispatch / lost count | Count outcome unknown; no generation; both arms stop |
| Complete count before generation | Unconsumed witness retained; no restart/replacement |
| Reservation before dispatch / lost acknowledgement | Generation may not have occurred; reserved attempt is not an actual-call assertion |
| Wire permission committed, before transport / response persistence | Unique claim cannot be reacquired; possible send remains uncertain |
| Generation dispatched, response/usage lost | Full exposure remains; partial evidence retained; both arms stop |
| Usage committed, tool/result missing | Usage stays recorded; owner/history fence prevents resumed tools/model |
| Payment settled before tool/result persistence | Existing settled/delivered authority remains; no refund, repay or automatic reconciliation |
| Episode result committed, next arm not claimed | Quiescent witness retained; crashed owner cannot resume the other arm |
| Identical/conflicting usage replay | One settlement / durable stop, no accounting rewrite |

The five files are not a distributed transaction. Source/dependency/runtime,
schedule, rates, limits, initial fixtures and successive quiescent state hashes
are bound in enrollment. Checkpoints detect drift but do not protect against a
malicious local administrator or restoration of an older *complete* set of files.
Do not open retained historical state through initialization. Fresh initialization
requires a nonexistent directory; interrupted initialization is not repaired.
Comparison journal version 2 adds unique durable wire permissions and binds mock
versus live enrollment. It rejects version 1 instead of upgrading or reopening
old campaigns. Preserve those records with their reviewed source; this is not a
retained-database migration or a budget reset.

External cancellation is propagated with a fixed sanitized message after bounded
best-effort cleanup. SQLite lock waits and HTTP close waits are bounded, but the
30-second deadline is cooperative. Abrupt exit, repeated cancellation, a blocked
synchronous dependency or persistence failure can prevent a final result. The
durable owner and pending records still fence replay. There is no automatic or
implemented comparison reconciliation command; an operator must inspect a stable
copy and resolve evidence explicitly before any separately reviewed future work.

Only bounded, sanitized model-visible input/history, tool results, selected native
usage fields, explanations and fixed error codes are retained. Instructions and
schemas are in the immutable policy; transcripts and body hashes identify every
request. Sanitization/truncation of untrusted prose is labelled and applied before
it is reused in model history. Headers, credentials, hidden reasoning and raw
provider bodies are not retained. Original saved evidence is never a data source.

Financial safety is scored directly from buyer/payment/merchant records, separately
from task behavior, explanation truthfulness, tool efficiency, latency and estimated
cost. Explanations must be judged against the saved **visible** filtered catalog,
not hidden facts. Human judgments remain pending; mock or assistant annotations
are not human ratings. Mock latency includes local tools and persistence and says
nothing about provider latency. Three trials permit descriptive paired counts,
not a statistically established winner. Old Luna results are context, not the
fresh paired control. Scripted mocks demonstrate mechanics, not model quality.

## Validation and proposed live preflight

Focused tests exercise actual pinned Responses serialization, native Messages
mapping, count binding, all 48 episodes, direct authority charge counts, duplicate
settlement, conflicting replay, partial usage, refusal/truncation, cancellation,
abrupt subprocess exits, concurrent ownership/reservations and redaction. Run
`tests/test_comparison*.py` under the same verified OS network namespace used by
`scripts/validate_offline.py`. The full validator remains unchanged, including
subprocess isolation, locked dependencies, lint/format checks and repeated demos.
Local results are not hosted CI or live compatibility evidence.

The [separate preflight](paired-preflight.md) requires review of this source/policy,
independent acceptance and explicit transient-key authorization. It cannot send a
generation request or invoke a buyer tool. It counts the shared schemas and locally
validated synthetic history, with each measured payload bound durably. Count success
cannot establish standard/global generation routing, billing scope or model quality.
Any future generation requires separate authorization and a fresh reviewed campaign;
it must recount every evolving request, settle only complete validated usage, retain
uncertain exposure and stop both arms on ambiguity. No charged diagnostic generation
is hidden outside that envelope. Authenticated evidence, if separately approved,
belongs in a fresh sanitized report; offline results remain explicitly mocked.

Sources checked 2026-11-10: [OpenAI input counting](https://developers.openai.com/api/docs/guides/token-counting)
describes effective counts including formatting; [Luna model limits](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [pricing](https://developers.openai.com/api/docs/pricing) supply the separate
long-context card. [Anthropic counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)
is free and described as an estimate without cache logic; its API supports the
full schema/tool settings. [Haiku limits/rates](https://platform.claude.com/docs/en/models/haiku-5-5/overview),
[context accounting](https://platform.claude.com/docs/en/build-with-claude/context-windows),
[structured-output overhead/subset](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[native Messages](https://platform.claude.com/docs/en/api/messages/create) and
[cache allocations](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
support the protocol assumptions. The context-to-billable-exposure inference above
remains explicit; unknown schema overhead is not treated as a constant.
