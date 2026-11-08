# Phase 3B.6 — offline evaluation and call-budget preparation

Built on accepted offline commit `6198c97d5ad4e30ae9b1dff8380cbfdc8d11f128`.
Published at `bcaa94c0c2a8baa4abb673f3db25ac58450f0832`; its
[exact-SHA hosted run](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions/runs/42424403222)
passed 708 tests, isolation/subprocess checks, repeated demos and both evaluator suites.
It makes no live OpenAI or payment calls,
loads no credentials, and changes no dependency versions or retained databases.
It does not authorize a live pilot, UI, cloud infrastructure or deployment.
The later [Phase 3B.7 preparation](phase3b7.md) adds a separate sourced-price path
and pinned runtime; this milestone's original evaluator retains its synthetic rates.

## Executable evaluation

`scripts/evaluate_offline.py` requires a new output directory and verified OS
network isolation. The full existing validator also executes it after its original
repeated demos. One JSON artifact contains two separately labelled suites:

- `offline_x402` fixtures use the existing scripted buyer: a new synthetic purchase
  remains unknown/reserved; replay preserves identity and executes no additional
  signer/dispatcher/settlement calls; separate explicit operator reconciliation and
  delivery acceptance precede another truthful agent status; a later finality
  incident remains visible without reversing accepted accounting or delivery.
- `responses_mock_over_simulator` uses the pinned Agents SDK / Responses adapter
  through `httpx2.MockTransport`: a paid simulated purchase, its replay, and a
  response with missing usage. The last case preserves the settled purchase and
  freezes further model requests across restart. Independent mock-dispatch rows
  must match every serialized request reservation.

Scores read the buyer ledger, payment authority/double database and durable tool
events directly. The model explanation is excluded. A fabricated narrative cannot
earn success; a false financial report, duplicate authority call or missing tool
trace fails the corresponding check. Reads take individual explicit SQLite
snapshots after writers have stopped; there is no cross-file atomic-snapshot claim.
This harness is for quiescent disposable fixtures, not monitoring a live system.

The artifact retains fixture labels and `blockchain_settlement_verified=false`.
Signer/dispatcher/settlement-double counts and simulator charges prove only the
instrumented offline behavior. Scripted replies and mocked usage do not establish
real-model quality, real token consumption or provider billing.

## Complete request boundary

`BudgetedMockTransport` checks the actual serialized HTTP request body produced by
OpenAI 3.42.1 and Agents SDK 0.24.0, immediately before invoking its in-memory
responder. The size includes instructions, all strict tool schemas, the structured
output schema, accumulated tool calls/results and model options. The existing
runner's 32 KiB input-plus-instructions check remains only an earlier coarse guard;
it is not described as a complete request or token bound.

The mock guard requires the configured model, reasoning effort and default tier,
512 or fewer output tokens, sequential tools, strict schemas, disabled storage and
truncation, and the existing four tool names. Both SDK layers have zero retries.
Only `https://responses.invalid/v1/responses` is accepted and its only transport is
the injected in-memory mock. There is no live transport option, environment-key
lookup, CLI backend switch or fallback. `OfflineX402Buyer` is explicitly rejected
by this Responses-mock preparation API; its scripted-only restriction is preserved.
The simulator CLI/default serialization and original buyer integrations are unchanged.

The configured example uses `gpt-6-luna`, reasoning `none`, tier `default`, three
invocations, at most six actual requests each (18 total), six SDK turns, 512 output
tokens/request, eight tool attempts and a 30-second cooperative timeout. Each bound
can be lowered. No comparison model or Bedrock path is implemented.

## Durable batch ownership and planning reservations

The small `CallBudget` SQLite file owns only API-call planning state, not payment
authorization or simulator money. It is explicitly created with exclusive file
creation, immutable policy and a version marker. Open requires an existing compatible
file; it never creates a missing batch or upgrades unknown history. It uses SQLite's
rollback journal and a 100 ms write-lock wait. No additional service is introduced.

An invocation consumes its durable slot before entering the runner. A unique owner
capability becomes available in memory only after that commit. Reopening cannot
recover it or continue an interrupted invocation. A complete serialized request
must atomically claim its request slot and reservation before hypothetical dispatch.
Concurrent claims serialize under `BEGIN IMMEDIATE`; a pending request prevents
another request. No transaction spans a model await.

The reservation uses explicit assumptions: body bytes × configured tokens-per-byte
plus framing allowance, full uncached input cost plus a cache-write surcharge, and
the maximum output allowance. The supplied rate card is explicitly **synthetic
planning data**, expressed in USD nanounits. Wire bytes are not exact billed tokens,
and the estimate is not a guaranteed dollar cap. Provider/model-specific hidden
framing, pricing changes and unknown cache lifetime remain future preflight concerns.

Observed input/output/total tokens, cached reads, cache writes and reasoning tokens
are validated as nonnegative strict integers. Total must equal input plus output;
cache reads and writes are non-overlapping subsets of input, and reasoning is a
subset of output. Missing or malformed fields remain unknown, with any individually
valid partial categories retained. The pinned usage schema exposes aggregate cache
writes but no write-lifetime split: the report explicitly labels lifetime unknown,
and the synthetic surcharge is a conservative planning assumption, not exact billing.
Unrecognized extra categories stop accounting rather than being silently ignored.

Returned model and processing tier must exactly match the immutable request policy.
Missing, malformed or mismatched identity retains the full reservation and blocks
continuation, even with otherwise valid token counts. It never borrows the requested
model's rate assumptions. An observed reservation/output/input-estimate overrun also
freezes the batch. Known usage, accounting completeness and unknown total estimate
are separate fields; unknown total consumption/cost is never represented as zero.

## Interruption and recovery contract

| Boundary | Durable result and permitted behavior |
| --- | --- |
| Invocation/request transaction fails before commit | No corresponding claim; no mock dispatch |
| Claim commits, acknowledgement is lost | Claim remains; no automatic continuation or replacement |
| Process exits after invocation claim but before first request | Incomplete invocation blocks new invocation; no owner recovered on reopen |
| Request reservation commits before dispatch/response | Pending exposure retained; a possible call is not assumed unused |
| Timeout, cancellation or transport failure | One best-effort sanitized interruption write; reservation remains and batch stops |
| Response usage observed before interruption | Known categories persist; unknown requests retain reservations |
| Usage commits but invocation completion is lost | Known usage survives; the incomplete invocation still blocks restart |
| Model fails after simulated payment settlement | Payment/delivery records remain authoritative; no financial cleanup or retry |
| Missing/incompatible batch file | Reject; never initialize/reset implicitly |

Repeated cancellation, storage errors, process death and blocking I/O can prevent
cleanup. The pre-dispatch durable marker remains conservative when cleanup fails.
Timeouts are cooperative and cannot kill uncooperative Python or guarantee a billing
cutoff. Returned raw model/merchant bodies, prompts, tools, signature markers and
exception text are not persisted in call-budget records; only body size/hash,
allowlisted identity, usage, counts and fixed reason codes are retained.

There is deliberately no automatic resume, uncertain reservation release, retry,
batch reset or general recovery framework. Preserve an interrupted batch for operator
review. A future live recovery operation needs separately reviewed provider evidence
and explicit authorization. Deleting/restoring an older complete batch, mixed-version
writers, malicious trusted Python and loss of every history copy are not protected by
this local journal. Do not create a replacement batch to evade an unresolved budget.

## Validation and remaining prerequisites

Install the locked environment before isolation:

```bash
uv sync --locked
parent_netns=$(readlink /proc/self/ns/net)
env -u OPENAI_API_KEY -u OPENAI_ORG_ID -u OPENAI_PROJECT_ID \
  PYTHONDONTWRITEBYTECODE=1 \
  unshare --user --map-root-user --net -- .venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

To retain a standalone artifact, run `scripts/evaluate_offline.py` with the same
launcher and parent namespace, replacing the validator arguments with
`--parent-netns "$parent_netns" --state-dir /tmp/<new-directory>`.
The supplied state directory must not already exist. Runtime isolation is fail
closed and verified for subprocesses; `uv --offline` alone is insufficient.

Tests cover complete serialization contributions, request/invocation limits,
planning exhaustion, cached usage, unverified response identity, cancellation,
timeouts, separate-process crashes, missing history, overlapping claims and unchanged
payment-authority counts. Worker diagnostics are retained and printed in full on
failure; no serial startup barrier can expire while later workers are prepared.

The independent provisional review's missing wire-suite and response-identity gaps
are addressed by the executable suite and targeted regressions. Final validation
results are recorded separately after execution. No hosted run covers these
uncommitted changes; the green 6198c97 workflow covers only accepted Phase 3B.5.

The local [SQLite WAL-reset prerequisite](phase3b1.md#sqlite-prerequisite--live-use-blocked)
remains unresolved. These tests do not remediate it. Real signer interoperability,
external merchant/facilitator integration, authoritative chain observation and live
x402/model coupling remain blocked. A future tiny API pilot requires separate
authorization, verified current model/rate assumptions, a reviewed spending ceiling
and a live request adapter; none is enabled by this milestone.
