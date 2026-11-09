# Phase 3B.8 — guarded simulator pilot activation

Built on `4580e764cdac9925def8510be2e845c611de9af8`. The transport and compatibility
correction were independently accepted offline. An initial authorized pilot stopped
after one response with incomplete cache-write lifetime evidence; no tools or
simulated payments executed. Following the correction and separate bounded approval,
a fresh simulator-only pilot completed purchase, replay and refusal on 2026-11-08.
The original stopped history and reservation remain unchanged. No further live
calls are authorized by this milestone. No CLI default or x402 backend is changed; the offline
x402 buyer remains scripted-only. Wallet/signing, RPC/facilitator integration, UI
and deployment remain outside scope.

## One shared pilot envelope

`initialize_activation(new_directory)` verifies the selected Python 3.12.16 /
SQLite 3.53.1 runtime before creating fresh state. It binds a synthetic standard
report goal, a 10000-atomic simulated purchase grant and a separate zero-budget
refusal grant. Exactly `purchase`, `replay`, `refusal` are enrolled, in that order.
Replay uses the original purchase task. Execution accepts no new task, goal, grant,
batch or arbitrary service argument.

One `envelope.sqlite3` owns the entire pilot's immutable enrollment, planning
policy, exclusive invocation claims and request/usage journal. All scenarios share
one USD 0.25 planning ceiling, three invocations and 18 request slots. A different
scenario/batch cannot obtain another allowance through this path. Limits may be
lowered at initialization; no reset/resume/replacement operation exists.

The simulator stores retain all payment/delivery authority; `agent.sqlite3` owns
agent history. The envelope is a final ready marker, committed atomically with its
policy and empty journal after financial/metadata initialization. Initialization
across files is not atomic: preserve partial directories for operator inspection.
Missing database files, altered schemas or missing/changed original agent bindings
reject on reopen. Read-only WAL checks can touch transient SQLite coordination
sidecars; they do not authorize schema repair or financial writes.

Enrollment binds the resolved root, original task/goal witnesses and schemas. The
approval hash also binds the exact policy. `BEGIN IMMEDIATE` and durable owners
serialize claims across processes. A crash after ownership commits fences the
whole pilot; losing an acknowledgement never creates another attempt. These are
guarantees within the retained approved directory, not a machine-wide approval
registry. An administrator copying/deleting/restoring complete histories or using
other trusted Python APIs remains outside that boundary.

Schema and goal witnesses are trusted-host configuration checks, not proof of file
identity or complete financial history. Schema hashes cover DDL and schema versions,
not row contents. They cannot detect replacement with another same-schema database
or restoration of an older complete backup. Preserve the original files and stop
for operator review if their history is uncertain; no new recovery protocol is
provided by this activation.

## Explicit activation and request boundary

The host invokes `run_live_scenario(root, scenario, api_key=SecretStr(...),
approved_enrollment_hash=...)` for one scenario after review and bounded approval.
The hash binds configuration; it is not cryptographic proof of human permission.
Nothing activates on import, key presence or a mode flag. No key is discovered,
file sourced, or automatic scenario batch executed. The existing ignored project
`.env` route is operator-only: an explicitly authorized host may parse it as data
and pass the key transiently. Both authorized attempts used this route; offline
preparation, correction and closure validation do not load credentials. No new
persistent credential configuration is required.

The existing runner accepts only the exact guarded-model handoff for this injected
live path. Arbitrary injected live models still reject, and the legacy unguarded
live-client factory is not used. Reports/durable records say `live` and `openai`
for this route. HTTP mocks testing that contract do not establish model quality.
The four tools, deterministic eligibility and financial semantics are unchanged.

Every serialized request must be `POST https://api.openai.com/v1/responses`.
Alternate origins, paths, query strings and transport extensions reject. The
transport rebuilds headers/routing after a durable reservation. TLS verification
is enabled; environment proxies/CA overrides, redirects, fallback models and
connection/API/SDK retries are disabled. Ambient `OPENAI_CUSTOM_HEADERS` is rejected
by name without reading its value; organization/project/admin credentials are not
used. The real key never enters the SDK client or request body. It is transiently
used in the wire authorization header, not journaled or printed. Memory zeroization
and protection against hostile process administrators are not claimed.

The 32 KiB check covers the complete body: instructions, strict tools/output schema,
options and all history. Only text, function-call/output and bounded reasoning
items are admitted. Configuration updates, additional/external tools, images/files,
stored conversations/previous response IDs, background processing, cache overrides
and unknown options reject before dispatch. Fixed settings: `gpt-6-luna`, reasoning
`none`, tier `default`, at most 512 output tokens, six requests/SDK turns, eight tool
attempts, sequential calls and a cooperative 30-second invocation timeout.

The pinned SDK can replay function-call items with `async_`, `caller` and `namespace`
set to JSON null. These exact optional fields are accepted only when absent or null;
every non-null value and unknown field still rejects before reservation/dispatch.
The guard does not rewrite the body: its complete original bytes, including those
null fields and tool-result call IDs, are counted and hashed by the shared envelope.
The in-memory HTTP tests exercise this pinned SDK contract. The separately
authorized pilot below also completed live continuations; neither establishes
general model reliability.

Raw responses are received incrementally in 4 KiB application chunks with a 64 KiB
limit before SDK interpretation; encoded responses are refused. Oversized bodies,
non-success status, malformed JSON, missing/unknown usage and model/tier mismatch
retain conservative exposure. Response headers/cookies are not forwarded. Fixed
codes replace error/cancellation payloads; SDK/HTTP diagnostics and tracing are
disabled. Response and connection cleanup is cooperative and best effort, including
under repeated cancellation, persistence failure or blocking I/O.

## Cost, interruption and recovery

Official [Luna documentation](https://developers.openai.com/api/docs/models/gpt-6-luna),
[pricing](https://developers.openai.com/api/docs/pricing) and
[cache usage](https://developers.openai.com/api/docs/guides/prompt-caching#monitor-cache-performance)
were rechecked at the authorized 2026-11-08 preflight. The frozen Standard
short-context rates matched: USD per million tokens, ordinary input 0.10, reads 0.01, 30-minute writes
0.125, 12-hour writes 0.25, output 0.50. Positive cache writes require both valid,
disjoint lifetime counts summing to the aggregate. Reasoning is included in output;
unknown categories are not silently priced. Sanitized observed usage and derivation
provenance are persisted before the SDK sees the response; raw response bodies are
not retained.

### Prospective zero-write allocation

The official cache guide defines nonnegative, disjoint lifetime allocations that
sum to `cache_write_tokens`. It also says both lifetime fields are returned,
including zero; it does **not** specify that absent/null details mean zero. Our
narrow accounting rule is an inference from an explicitly reported integer zero
aggregate, not a provider guarantee or an observed lifetime value.

For a newly observed Luna response with verified model/tier, supported input size
and otherwise valid usage, an absent or null **entire**
`cache_write_tokens_details` field permits deriving two zero allocations. The
observed `ttl_30m_tokens` and `ttl_12h_tokens` remain `null`; the separate
`cache_write_allocation` record retains bounded aggregate/container/leaf type and
presence tags, an unknown-field flag and explicitly labelled `derived_tokens`.
Only the ephemeral pricing input uses those derived zeros. The immutable rate
card is unchanged. Complete usage/accounting in this case means sufficient
observed-plus-derived evidence, not that every raw field was supplied.

Boolean, string, floating-point, negative or missing aggregates never qualify.
An object with missing/null/malformed leaves, unknown fields, or positive allocations
contradicting a zero aggregate still rejects. Nonzero totals with absent/null
allocations, inconsistent sums, other unknown usage categories and model/tier
mismatches remain fenced before tools/continuation. This rule does not alter the
serialized request guard, response bounds, request/cost limits or restart fences.

The stopped response retained 510 input / 15 output tokens, zero aggregate writes,
zero cached reads and zero reasoning tokens. Its normalized journal merged
missing/null/invalid lifetime details into unknowns; no raw body was retained.
Regression inputs are reconstructed variants, not a byte-exact captured response.
Reopen/report never re-normalizes old rows, and observation cannot replace an
already incomplete row. The original one-request stopped envelope remains blocked
with its **USD 0.001239** planning reservation and unknown accepted total estimate.
It is neither repriced nor released. The subsequent explicit approval allowed only
17 additional requests and USD 0.248761, retaining that exposure within the original
USD 0.25 / 18-request allowance. The correction itself granted no spending authority.

The maximum 18-request plan is USD 0.156672 at the conservative 12-hour-write rate,
or USD 0.0654336 using ordinary input, under the existing one-token-per-byte plus
1024 framing-token assumption. USD 0.25 was approved once for the whole pilot.
This is not an exact token bound, provider-enforced billing cap, verified credit
balance or future-price guarantee. Across the stopped and fresh attempts, preserving
the combined allowance is a trusted-host/operator responsibility; the envelope is
not a machine-wide budget registry. Neither a new task nor a successful result
authorizes resetting it.

Unknown dispatch/usage and any non-completed invocation stop the whole pilot.
A crash before dispatch may retain an unused reservation; never resend it.
Cancellation before the first request still stops subsequent scenarios. Known
partial usage survives; unresolved totals remain unknown. Model failure after a
simulated payment leaves its settled/delivered state intact. Cancellation propagates
after best-effort persistence/cleanup; process death can leave a running claim.
Successful scenario checks use payment-authority rows and original purchase state,
not model-generated success prose. There is no automatic polling, retrieval,
reconciliation, release, retry, repayment or backend switching.

## Sanitized authorized pilot results

The fresh pilot used one reduced immutable envelope for all three scenarios, with
17 shared request slots and a USD 0.248761 planning allowance. The original
USD 0.001239 reservation was retained separately. Each scenario was inspected before
the next was permitted; there was no automatic retry or extra live scenario.

| Scenario | Responses requests | Tool attempts | Result | Estimated API cost (USD) |
| --- | ---: | ---: | --- | ---: |
| Purchase | 5 | 4 | One simulated purchase settled and delivered | 0.0004327 |
| Same-purchase replay | 2 | 1 | Same purchase and delivery; no additional execution | 0.0001358 |
| Zero-budget refusal | 3 | 2 | No suitable service; no purchase created | 0.0002039 |
| Fresh pilot total | **10** | **7** | All three invocations completed | **0.0007724** |

Every request specified `gpt-6-luna`, reasoning `none`, tier `default`, and at most
512 output tokens. All ten responses reported exactly that model and tier. The
six-request/six-turn, eight-tool and cooperative 30-second limits per invocation,
zero retries and disabled tracing remained in force.

Observed usage totaled 6424 input and 260 output tokens. Cached reads, aggregate
cache writes and reasoning tokens were zero. All ten responses omitted the whole
cache-write lifetime-details object: observed lifetime values remain `null`, with
two separately labelled derived zero allocations per response. New accounting is
complete under the prospective rule above, with no unresolved new reservation.
The estimate is `(6424 × 100 + 260 × 500)` nanoUSD = **USD 0.0007724**. It is not a
provider invoice or verified billed amount.

Direct payment-authority queries after each scenario recorded exactly one execution
attempt, one authorization and one settled simulated charge of 10000 atomic units.
The buyer retained one settled/delivered purchase with spent=10000 and reserved=0;
replay preserved its identity. The separate refusal grant remained budget=0,
spent=0 and reserved=0, with no purchase row. These are simulator records, not chain
settlement. The hardcoded signing statistic was not used as instrumentation.

The two attempts used 11 of the original 18 request slots. The new estimate plus
the preserved original hold is **USD 0.0020114**; this mixes an estimate and a
reservation, not a total billing estimate. Combined historical usage/accounting
remains incomplete because the old request is unresolved. Its journal was not
re-normalized, repriced, released or reset. The fresh pilot demonstrates this one
bounded integration path, not broad decision quality or prompt-injection resistance.

## Offline validation and limits

Offline validation uses in-memory HTTP doubles under verified OS network isolation,
including subprocesses. Tests exercise the pinned SDK, strict outbound routing,
shared limits, incremental receiving, concurrent ownership and interruption/restart.
Test keys are distinctive synthetic markers; credentials are never required.
The accepted implementation passed 884 tests, Ruff lint/format, lock validation,
repeated simulator/agent demos and the offline evaluator. A separate independent
run passed the 114 activation and zero-write regression cases. These are local
offline results, distinct from the authorized live evidence above and from any
subsequent exact-SHA hosted workflow. Candidate hashes and detailed operator evidence
are retained outside the public repository; no raw responses, credentials or runtime
databases belong in the publication payload.

The selected fixed runtime is mandatory. Old `.venv`/system SQLite remains
unpatched; an isolated or hosted success does not patch it. ARM64/cloud deployment,
real payments, authoritative chain observation and live x402 remain outside scope.
