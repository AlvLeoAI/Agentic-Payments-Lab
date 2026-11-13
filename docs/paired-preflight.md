> Step 2 offline update: generation reservation uses exact-request counted input
> plus an explicit assumed margin and one total output cap, at PLACEHOLDER example
> prices. Context-window calculations are uncounted fallbacks only, not counted
> reservation floors. See generation-compatibility.md. Historical preflight evidence
> is unchanged and does not authorize generation or verify account prices.

# Paired transport and bounded preflight

This candidate adds a separately guarded transport to the matched simulator host
loop. The historical viewer, Luna campaign, simulator financial authority and
live x402 prohibition are unchanged. There is no command-line live switch and
imports never discover credentials or construct clients. Tests use synthetic keys
and in-memory HTTP under verified OS network isolation, including subprocesses.
Neither those tests nor a successful count probe establish generation compatibility,
model quality, account billing or production readiness.

## Explicit stages

1. Review and freeze source, dependency/runtime witnesses, policy and rates. Run
   the full isolated validator and independent review before authenticated work.
2. With separate approval, initialize one fresh preflight journal. Supply only the
   existing project keys transiently, parsed as configuration data, to their own
   issuing providers. Do not source shell files, search other credential stores,
   create credentials, or record secrets in command arguments, reports or logs.
3. Run at most six non-generating requests in the frozen order below. Stop on the
   first failure or inconsistent evidence. No retries, fallback or resume.
4. Inspect the sanitized result and remaining assumptions. Generation requires
   separate operator approval, pricing/scope/backstop review and fresh live
   comparison enrollment bound to the completed preflight/source/result hashes.
   A successful preflight never calls or automatically authorizes the generation
   runner. No charged diagnostic generation is part of preflight.

| Order | Provider | Operation |
| --- | --- | --- |
| 1 | OpenAI | `GET https://api.openai.com/v1/models/gpt-6-luna` |
| 2 | Anthropic | `GET https://api.anthropic.com/v1/models/claude-haiku-5-5` |
| 3, 5 | OpenAI | `POST https://api.openai.com/v1/responses/input_tokens` |
| 4, 6 | Anthropic | `POST https://api.anthropic.com/v1/messages/count_tokens` |

The first count includes the full instructions, four strict tools, final schema,
fixed model controls and synthetic initial message. The second also contains all
four schema-valid synthetic tool calls/results, including linked `quote_id` and
purchase identity. Pydantic validates these values before enrollment. No buyer
tool is executed and no financial databases are created by preflight. Count
requests include only controls supported by the respective count endpoint; they
cannot verify generation service tier, output cap or geographic routing.

The OpenAI SDK remains pinned at 3.42.1 with Agents SDK 0.24.0; the Anthropic path
uses native HTTPX JSON, `anthropic-version: 2023-06-01` and the still-supported
`x-api-key` header. No dependency/version upgrade is needed. Keys requiring an
additional workspace selector are not silently rerouted or repaired: a failure
stops the preflight. OpenAI organization/project overrides are blank; returned
scope headers, when present, are hashed and checked for consistency. Missing
headers remain unknown, not proof of account identity or full key scope. Material
routing, identity or pricing ambiguity requires operator review before generation.

## Admission, transport and evidence

`run_mock_preflight` accepts only exact `httpx2.MockTransport` instances, before
ownership or authentication is constructed. It rejects real transports, factories
and subclasses. Mock responder callbacks are trusted Python test code, not a
sandbox. `run_authenticated_preflight` accepts only authenticated enrollment and
constructs the fixed TLS-verifying transport internally. No injectable production
factory exists. Generation's separate `run_live_comparison` also requires reviewed
live enrollment; default comparison execution stays mock-only.

Each preflight attempt commits an exact provider/method/endpoint/body-hash intent,
then unique dispatch permission, before authentication is built. Generation uses
the existing count/reservation admission followed by a unique wire claim in the
same comparison journal. These commits are sequential, not a single transaction
with the network. A gap conservatively fences continuation instead of resending.
SDK headers/extensions are discarded; only the matching transient provider key
and fixed headers are rebuilt. TLS verification is mandatory; environment proxy
settings, redirects, retries, regional overrides and custom headers are disabled.
Presence of `OPENAI_CUSTOM_HEADERS` fails closed without reading its value.
OpenAI auxiliary admin/webhook credentials are explicitly blank, never inherited.

Full request bodies remain capped at 32,768 bytes. Raw identity-encoded responses
are read incrementally: 16,384 bytes for model metadata, 4,096 for counts and
65,536 for generations. Responses are validated before SDK consumption. Known
response/usage evidence commits before close, so a subsequent close failure cannot
erase observed usage. HTTP errors, malformed bodies, credential reflection,
timeouts and cancellation stop both arms without releasing unknown generation
exposure. Oversized-input HTTP 400 remains an unverified attempt; native
`model_context_window_exceeded` is a distinct truncation outcome with validated
usage retained and no tool execution or retry.

Only selected usage/count/model fields, fixed error codes, request hashes and
optional scope hashes persist. Headers and raw bodies do not. Keys never enter
the SDK configuration; reflected keys are rejected before retention, SDK errors
are sanitized, remote tracing is disabled, and standard logging is suppressed
while transient authentication is handled. This suppresses unrelated logging
during overlapping wire calls too. It does not promise Python memory erasure,
protection against a malicious host/test callback, or safety from arbitrary
debuggers, core dumps or nonstandard logging handlers that bypass logging itself.

## Failure and recovery

| Interruption | Retained evidence and allowed next step |
| --- | --- |
| Owner acquired, before first reservation | Owner fence; no automatic restart |
| Intent committed, before wire permission | Unknown attempted outcome; no replacement |
| Permission committed, before/after transport invocation | Possible send; retain uncertainty and any generation exposure |
| Headers received, body missing/invalid | HTTP status retained; usage/count unknown |
| Complete response persisted, close fails | Complete observed evidence retained; campaign stops |
| Model stops after simulated settlement | Settled/delivered authority remains; no refund, repayment or reconciliation |
| Process exits after result, before next step | Owner/schedule fence; no next-arm resume |

External cancellation propagates a fixed sanitized message after bounded
best-effort cleanup. The 30-second request/episode timeout is cooperative, not a
hard process deadline; blocking dependencies, abrupt exit, repeated cancellation
or persistence failure can prevent cleanup. HTTP close waits are limited to 0.5s
and SQLite lock waits to 0.1s. Pending owner/attempt records still prevent restart.
Actual sent-call counts cannot always be reconstructed from a pre-dispatch intent;
reports distinguish reserved attempts, received HTTP responses, verified results
and unknown outcomes. No consumption is invented as zero.

The preflight journal is version 2 and fresh-only. It rejects version 1 without
migration or diagnostic backfill. The comparison journal is
version 2; it rejects version 1 without migration. Neither restores or repairs
historical campaigns. The separate financial/metadata files are not a distributed
transaction; stable source/state witnesses detect drift but cannot defeat a
malicious administrator or restoration of an older complete set of databases.
Operator investigation uses stable copies; no recovery runner or automatic budget
reset is provided.

## Bounded rejection diagnostics

The stopped version-1 preflight validated both model reads, then received HTTP 200
for the first OpenAI count without committing an accepted count or rejection code.
Its response body and exact failure category were not retained. New diagnostics
cannot recover that missing evidence or identify its cause. Preserve that journal,
its invocation marker and recorded byte hashes; never resume or rewrite it.

Fresh version-2 journals retain at most one immutable first-failure record:
allowlisted `stage` and `reason`, a slot from 0–5 (or unknown), the durable attempt
status (or unknown), and an observed HTTP status from 100–599 (or unknown).
No raw body, header, sensitive identifier, remote field name, free-form exception
text or credential is added. Secondary cleanup cannot replace the first failure.
Diagnostics and stopping are separate best-effort writes; failure of either write
cannot erase accepted observations or release pending exposure. Reports explicitly
mark unavailable/unrecorded diagnostics and preserve the owner/restart fence.

| Failure group | Examples of fixed reason codes |
| --- | --- |
| Admission / dispatch | `admission_rejected`, `transport_failed` |
| HTTP / body | `http_status_rejected`, `encoding_rejected`, `response_limit`, `body_read_failed` |
| Decoding / JSON | `decoding_failed`, `json_invalid` |
| Response / model / count | `response_contract`, `model_mismatch`, `count_fields`, `count_type`, `count_bounds`, `count_object` |
| Scope / reflection | `scope_invalid`, `scope_presence_changed`, `scope_value_conflict`, `credential_reflection` |
| Persistence / cleanup | `persistence_failed`, `close_failed`, `cancelled`, `timeout`, `unknown_exception` |

Stages distinguish reservation, dispatch permission, HTTP-status persistence,
body/validation, observation persistence, response/client close, finalization and
completion acknowledgment. The first observed cancellation may come from external
cancellation or a cooperative timeout; a cancellation code does not establish
which caused it. Unknown exceptions never acquire a guessed category from their
message. HTTP-status persistence failures retain the separately observed status
when available, even if the attempt row could not be updated.

Count/type validation is unchanged. Scope metadata is endpoint-aware: optional
header presence must agree between repeated probes of the same exact endpoint.
Model-read and count endpoints may expose different subsets, including no headers.
Every overlapping header value must still agree across all completed observations
for that provider, including across endpoints; conflicts and invalid values stop
the run. Per-attempt hashes remain separate. No union of partial observations is
promoted to verified billing identity or full key scope, and missing headers remain
unknown. Same-endpoint presence changes retain `scope_presence_changed`.

This corrects an unsupported assumption that different endpoints expose identical
optional metadata; it does not establish why any provider omits a header. The
fresh version-2 diagnostic rejected the first OpenAI count for a cross-endpoint
presence difference after two successful model reads. The rejected headers and
count were not retained and remain unknown. The older version-1 failure still has
no established cause. Neither stopped journal is resumed, migrated or rewritten.
The installed OpenAI count type expects `input_tokens: int` and
`object: response.input_tokens`; no mismatch with either stopped response has been
demonstrated. This source change requires new independent review and separately
authorized fresh enrollment before any further authenticated probe.

An explicit completion acknowledgment commits only after all six responses have
validated, all HTTP clients have closed, and finalization has returned normally.
The legacy `finished` state alone is insufficient. If finalization commits and
then raises while diagnostics cannot persist, the acknowledgment stays absent,
and reopening remains incomplete and cannot authorize generation. Recorded failure
also blocks acceptance even when all response observations are complete.

The final acknowledgment defines an honest durable boundary, not a claim that all
future reporting succeeds. If it commits and a later error cannot be recorded,
reopening proves only the work before that boundary. `no_failure_recorded` is not
proof that no failure occurred; `post_completion_reporting_verified` remains false.
A caller that observes the error reports incomplete/unknown and its report hash
does not match a later snapshot lacking that process-local observation. Preserve
that caller report and require operator review; do not infer error-free cleanup or
reconstruct uncommitted errors. Abrupt exit and simultaneous persistence failure
remain limits. No automatic restart, recovery or fresh probe is added.

## Pricing and remaining assumptions

Official sources checked 2026-11-10 state that [OpenAI input counting is free](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens)
and [Anthropic token counting is free](https://platform.claude.com/docs/en/build-with-claude/token-counting).
The former counts effective input, while the latter describes an estimate without
cache execution. Neither predicts generated output or proves account billing.
Preflight reports therefore retain published count fee zero separately from
unknown observed billing cost, key scope and account credits. No billing dashboard
or administrative account API is called.

The matched experiment remains 48 episodes, 144 generation attempts per arm/288
total, USD 1.50 per arm/USD 3 total without transfer, six generations/eight tools
per episode, 512 maximum output tokens and a 30-second cooperative timeout. Its
independent count bounds and exact settings remain unchanged: Luna reasoning none,
default tier; Haiku thinking disabled, medium effort, standard-only/global.
The conditional context backstops (USD 0.525384 / USD 1.001280) remain distinct
from measured input estimates and actual observed usage; they are not vendor
guarantees or hard billing caps. Complete valid usage resolves exposure; unknown
usage holds it and stops both arms. Full input totals include cache creation/read
tokens before Haiku's 100,000-token pricing threshold, without double-counting TTL
allocations. Human explanation judgments remain pending.

The full local validator also checks locked dependencies, Ruff, formatting,
subprocess isolation, repeated demos, direct payment-authority idempotency,
evaluations and the viewer. Hosted CI is separate evidence. Its earlier
20-minute job timeout proved too tight for this expanded suite plus dependency
installation (one run was cancelled at the limit), so it is now 30 minutes;
tests and fail-closed isolation are not weakened to obtain green CI.
An authenticated probe result is also separate evidence and cannot establish that
a hosted run passed or that the operator's older system SQLite was fixed.

The subsequent [offline generation compatibility review](generation-compatibility.md)
corrects Luna's conditional reservation to use the full documented context allowance.
Its separate two-generation mock rehearsal is not a live runner or an approval to
reuse the successful preflight with changed source. Historical evidence stays intact.
