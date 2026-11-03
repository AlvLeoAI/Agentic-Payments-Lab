# Phase 2: bounded buyer over simulated payments

The official Python Agents SDK runs one buyer with Responses-format model items.
The deterministic offline model uses the same `Runner.run`, strict function tools,
output schema and limits as the explicit live Responses adapter. Initial implementation
and corrective validation were offline. A separately authorized free-service live
smoke is recorded below. Mocked HTTP responses test the wire contract; they do not
prove model availability, account access or live behavior.

## Verified SDK contract

Inspected on 2026-11-02: `openai-agents==0.24.0`, `openai==3.42.1`, Python 3.12.
Both direct dependencies are pinned; `uv.lock` records all transitive packages.
The existing FastAPI 0.162.0, HTTPX 0.28.1 and Pydantic 2.14.1 pins are preserved.
OpenAI 3.42.1 uses HTTPX2 internally (locked 2.16.0); the merchant still uses
HTTPX ASGI exclusively. SDK transitive MCP/server libraries install, but no MCP
server, hosted tool, handoff, shell or filesystem tool is configured or exposed.

Official documentation read before implementation:

- [SDK installation and selection](https://developers.openai.com/api/docs/libraries#use-the-agents-sdk)
- [Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart)
- [Agent runner lifecycle](https://developers.openai.com/api/docs/guides/agents/running-agents)
- [Strict tool schemas](https://developers.openai.com/api/docs/guides/function-calling#strict-mode)

Installed SDK interfaces were inspected for `Model`, `ModelResponse`, `FunctionTool`,
`ModelSettings`, `OpenAIResponsesModel`, `RunConfig`, retry control and tracing.
The implementation explicitly sets `parallel_tool_calls=False`, local tool concurrency
to one, `store=False`, disabled truncation, and zero SDK/client retries. There is no
model alias selection or fallback. Remote tracing and export processors are disabled;
SDK/HTTP diagnostic output is suppressed in favor of application event codes.

Operator configuration accepts `--reasoning-effort` and `--service-tier`, validated
against the pinned SDK's enumerated values. Both default to omission, preserving
existing model/project behavior; model-specific compatibility remains the operator's
responsibility. Reasoning uses `ModelSettings.reasoning=Reasoning(effort=...)`; the
processing tier uses the SDK's supported `ModelSettings.extra_args` forwarding.
For the bounded GPT-6 Luna smoke, explicitly pass `--reasoning-effort none
--service-tier default` (Standard). HTTP-mock tests inspect every serialized request,
including continuations, and count actual transport attempts at turn exhaustion and
on retryable HTTP errors. These tests do not establish live model behavior.

## Responsibilities and state

`contracts.py` defines immutable operator inputs and minimal results. `boundary.py`
uses public `BuyerSession` operations. Read-only `context`, `constraints` and
`status_or_none` additions supply immutable identity, one-row grant/accounting state,
and an explicit `not_started` result before purchase. Financial write methods and
the Phase 1 state machine remain unchanged.

`store.py` owns an additional local `agent.sqlite3`, created only for agent commands:

- Immutable task/goal bindings (subject, purpose, required quality and task identity).
- Complete quotes plus request bindings, addressed by task-scoped handles.
- Sanitized tool events and incremental run records, excluding model prose.

This metadata has no financial authority. The buyer ledger also holds an immutable
copy of each original agent binding, separate from grants and financial transitions.
There is no transaction spanning the stores. Losing a quote-handle write leaves an
unused quote, not a payment. Agent metadata failure cannot undo payment or release
exposure. When a final snapshot can be read but not persisted, the returned report
sets `metadata_persisted=false` and `termination=metadata_error`. If storage cannot
be opened/read, use Phase 1 status/audit; do not assume that a report was written.

## Initialization and recovery

Before first agent use, the operator runs `agent-init TASK --subject ... --purpose ...
--quality ...`. This is an explicit attestation that the existing Phase 1 task has
no prior agent history. The task must exist and have no purchase. Existing grants,
task identity and financial behavior remain unchanged. Execution through `agent`
never initializes or repairs bindings. Running without enrollment returns
`agent_initialization_required`; missing/inconsistent metadata for an enrolled task
requires recovery. Changed subject, purpose or quality is rejected against the
ledger witness even when the agent database/binding row is missing. Repeating a
valid initialization is harmless, but it cannot repair missing metadata.

Initialization commits the original full context/goal to `ledger.sqlite3` first,
then writes its matching metadata copy. Failure between these writes leaves a
witness without metadata, which blocks execution until explicit recovery. These
writes are not cross-database atomic. The ledger witness is immutable through the
application and SQL triggers; it is not protected against a filesystem administrator.

For missing `agent.sqlite3` or a missing binding row:

1. Stop agent processes and preserve the surviving databases and any recoverable
   metadata/WAL evidence. Inspect Phase 1 `status`/`audit` independently of agent history.
2. Run `payments-lab --state-dir PATH agent-recover TASK`. This command takes **no
   replacement goal**. It restores only the original binding from the ledger witness;
   it does not execute/reconcile payments, create tasks, or recreate quotes/run usage.
3. Run the agent with the original goal. Reports carry `history_incomplete=true`
   after binding recovery. Lost run/token history remains unknown. Existing purchases
   return their current state; any required reconciliation remains an operator action.

Recovery refuses to overwrite conflicting metadata. Restore a verified consistent
copy or obtain a separate reviewed recovery decision instead of deleting evidence.
A missing/corrupt ledger witness is also a stop condition. Pre-correction agent
metadata has no independent witness: automatic adoption is intentionally unsupported.
Keep those stores and use Phase 1 inspection until the original intent can be verified.
History lost *before* this witness existed cannot be detected retroactively from an
empty store. Never use `agent-init` to assert first use for an ambiguous legacy task.
The initialization contract requires that explicit operator knowledge; caller goal
arguments are not recovery evidence. This is not a general backup/migration framework.

## Run evidence and cancellation

`runs.data` contains the task/purchase identity, termination, authoritative purchase
snapshot and observed usage. A `running`, `run_complete=false` row is committed before
any model request. Each attempt is persisted before dispatch with pending usage;
each response's observed tokens are persisted before its tool can run. Tool completion
also refreshes the purchase snapshot. Start/progress write failure stops further
model dispatch. Repeated invocations create new run IDs without overwriting old evidence.

`requests_started` counts recorded attempts; a hard crash in the small gap before
HTTP dispatch may count an attempt never sent. `responses_received` counts responses
observed by the wrapper. `input_tokens` and `output_tokens` are known partial totals,
not estimates of missing consumption. `unresolved_requests` counts attempts lacking
complete observed usage (pending, failed or missing API usage). A nonzero count sets
`incomplete=true`; zero known tokens with that flag does **not** mean zero API cost.
API cost remains null. Usage may be complete if cancellation occurs between requests
after all response usage was observed, even though the agent task did not finish.

External cancellation attempts to persist `termination=cancelled`, `run_complete=true`
and the latest authoritative payment/delivery state, then propagates `CancelledError`.
Cancellation is an agent termination outcome, not a payment outcome: a run may be
cancelled while its purchase is settled/delivered, or while exposure remains reserved.
No model continuation, payment retry, reservation release, cancellation of payment,
or reconciliation occurs during cleanup.

The cancellation snapshot is attempted before awaiting client close. Metadata writes
and this snapshot's ledger read use 100 ms SQLite lock waits; client close gets a
500 ms cooperative timeout. A repeated cancellation may interrupt close; only one
additional snapshot is attempted, with no retry loop. Persistence failure may leave
only the last `running` record with unresolved usage, rather than a cancellation
outcome. Abrupt process exit/SIGKILL skips cleanup entirely: the last committed run
row survives if storage survives, but its purchase snapshot may lag the ledger and
its usage may omit an observed but not yet committed response. Consult Phase 1 state;
never interpret `run_complete=false` as a safe instruction to resume payment. A task
cancelled before its coroutine begins has made no request and may have no run record.
Filesystem stalls, uncooperative coroutines, process death or storage loss can defeat
cleanup; these are bounded lock/async waits, not a hard real-time guarantee.

`runner.py` owns SDK configuration, limits, usage and termination reporting.
`scripted.py` implements a deterministic SDK `Model` for offline demonstrations.

| Model tool | Accepted arguments | Result |
| --- | --- | --- |
| `list_services` | none | Eligible services, cheapest choice, quality and budget/cap summary |
| `get_quote` | `service_id` only | Task-scoped quote handle, service and integer amount |
| `purchase` | `quote_id` handle only | Authoritative payment/delivery summary |
| `get_purchase_status` | none | Same summary, including `not_started` |

The application enforces cheapest eligible selection using quality rank, allowed
services, purpose, active/non-revoked grant, cap and available budget. Free wins when
sufficient. No suitable service leaves the purchase unstarted. These summaries are
advisory snapshots: the existing transactional policy still authorizes every purchase.
Quote handles are not payment authorization. Fabricated/cross-task handles fail;
arguments containing subject, amounts, recipients, receipts or other extra fields fail.

An existing purchase returns its recorded state; the buyer never retries execution
or delivery, cancels, reconciles, or creates a replacement. Repeating a CLI command
uses the same task/purchase and immutable goal. Operators retain explicit reconciliation.

## Limits, privacy and residual risks

- At most eight tool invocations are permitted per run. Invalid arguments and
  unavailable tool names count as attempts. The rejected ninth attempt is reported
  but never invoked. A returned parallel batch is rejected before executing any tool.
- Separate SDK turn limit: default 10, configurable 1–16. Timeout: default 30 seconds,
  configurable up to 120. Async cancellation cannot interrupt synchronous SQLite
  work; financial operations retain their existing 30-second busy timeout and can delay
  cancellation delivery. Setup/finalization are outside the runner timeout. Financial
  state is retained; the shorter cleanup waits above do not create a hard deadline.
- Subject: 120 characters; identifiers: 100. Tool arguments: 1 KiB; tool output:
  4 KiB; model output items: 8 KiB; model conversation plus instructions: 32 KiB.
  Final explanation: 1,000 characters. API output limit defaults to 512 tokens,
  configurable 128–2,048. Size checks fail closed rather than truncate authority data.
- Merchant descriptions, subject text and delivered prose are not forwarded to the
  model. The model sees narrow structured service/quote/status results. These are
  still data, not instructions. Scripted attacks test host enforcement only;
  real-model prompt-injection resistance is unverified.
- Reports separate authoritative `purchase`/`decision` from untrusted
  `model_explanation`. Fabricated success prose cannot change recorded states.
  Failures retain reserved exposure and identify operator reconciliation when needed.
  An interrupted paid or free purchase awaiting delivery also requires the operator;
  the agent does not resume it.
- `model_usage` counts available input/output tokens separately from SIM-USDC.
  Offline usage is labeled scripted. Missing usage or an interrupted model request
  marks totals incomplete. API cost is not calculated; request limits are not a
  hard dollar budget. Failed/timed-out requests may still incur API charges.
- Durable events omit arguments, exceptions, raw receipts, content and model prose.
  Bindings still contain subject/purpose; local files are sensitive, unencrypted and
  retained until operator cleanup. No remote trace export or model-session persistence
  is configured. `store=False` does not promise zero data retention by the API service.
- One trusted host/process boundary remains assumed. This is not a Python sandbox.
  Live credentials must never be exposed as agent tools or included in shell history.

## Bounded live smoke configuration

First select and approve an account-accessible Responses model supporting strict
function calls and structured output. Set `OPENAI_API_KEY` securely in the environment
and `OPENAI_MODEL` to that exact model. Do not paste credentials into commands or chat.
The CLI does not load `.env` files, use ambient base-URL overrides or proxy settings,
or fall back to another model. An operator launcher may parse the ignored `.env` as
configuration data and supply the key privately; never source it as shell code or
log its values. Offline validation requires neither the file nor credentials.

After approval, use a new state directory and a zero simulated-spending grant:

The example `init` command below allows all catalog services, subject to its zero
budget and cap. For a **report-free-only** smoke, create the grant through the
existing operator `Grant`/`Ledger.add_grant` API with `service_ids=("report-free",)`
instead of `init`. Keep the subsequent task creation and explicit `agent-init`.

```bash
uv run --offline --no-sync payments-lab --state-dir .lab/live-smoke init --budget 0 --cap 0
uv run --offline --no-sync payments-lab --state-dir .lab/live-smoke task live-smoke
uv run --offline --no-sync payments-lab --state-dir .lab/live-smoke agent-init live-smoke \
  --subject synthetic-smoke --purpose research --quality basic
uv run --offline --no-sync payments-lab --state-dir .lab/live-smoke agent live-smoke \
  --mode live --model "$OPENAI_MODEL" --subject synthetic-smoke --purpose research \
  --quality basic --timeout 30 --max-turns 6 --max-output-tokens 512 \
  --reasoning-effort none --service-tier default
```

`uv --offline` only disables dependency access: this explicitly live command makes
billable OpenAI requests. It is not run in the isolated validator. Proposed checks:
free report or honest no-purchase outcome; zero payment-authority attempts/charges;
at most six model requests, zero automatic retries, 512 output tokens per request
and a 30-second cooperative timeout; no remote tracing; reported model usage;
unchanged purchase identity on any later repeat. A zero SIM-USDC budget does not cap OpenAI API cost.

On 2026-11-03, one separately authorized smoke completed with `gpt-6-luna`, explicit
reasoning `none` and Standard (`default`) processing. All five actual Responses
requests carried these settings; all five responses reported the requested model
and tier. Four model-initiated tools delivered the correct basic `report-free`
resource for the synthetic subject. The payment authority recorded zero executions
and charges; the buyer ledger showed `not_required` / `delivered`, with zero spent
and reserved. Durable usage was complete: 3,476 input and 144 output tokens, no cache
reads/writes or reasoning tokens reported. The run completed in 9.71 seconds with
no retry or rerun. Configuration verification also passed 151 isolated tests.

## Bounded Phase 2 acceptance

On 2026-11-03, three additional authorized live invocations passed with the same
model and explicit settings above. Each had six requests/turns maximum, zero retries,
512 output tokens per response, eight tool attempts and a 30-second cooperative
timeout. The complete pass was limited to three invocations/eighteen requests;
it used ten requests, with no reruns or manual purchases. All responses reported
`gpt-6-luna` and `service_tier=default`. Replay and refusal both used actual Responses
calls; neither was an application-only short circuit.

| Scenario | Requests / tools | Authoritative result | Input / output tokens |
| --- | --- | --- | --- |
| Standard acquisition, budget/cap 10,000 | 5 / 4 | `report-standard`, settled/delivered; one execution/charge of 10,000; spent 10,000, reserved 0 | 3,548 / 138 |
| Replay the same task and binding | 2 / 1 | Same purchase, receipt and resource despite exhausted budget; cumulative execution/charge count remains one | 1,134 / 46 |
| Premium requirement, budget/cap 10,000 | 3 / 2 | No eligible affordable service; no downgrade, purchase or delivery; executions/charges/spent/reserved all zero | 1,750 / 59 |

All three terminated `completed`, with durable run records and complete usage
(zero unresolved requests). Direct payment-authority, buyer-ledger and provider
queries verified these outcomes independently of model narration. All payments and
receipts remain simulated; no blockchain evidence or real payment was involved.

Additional observed API usage totaled 6,432 input and 243 output tokens, with no
cache-read, cache-write or reasoning tokens reported. At the verified Standard rates
of $0.10/M input and $0.50/M output, the estimated cost was **$0.0007647**, separate
from simulated spending and not an inspected invoice. The conservative combined
preflight estimate was $0.19584 against the authorized $0.25 threshold; that estimate
was not a hard billing cap. Sources: [pricing](https://developers.openai.com/api/docs/pricing)
and [cache billing](https://developers.openai.com/api/docs/guides/prompt-caching).

Fresh local validation passed **151 tests in 58.61 s**, Ruff lint/format and lockfile
checks, plus all repeated simulator/agent demos under verified OS network isolation.
Hosted validation must be checked for the exact published commit in
[GitHub Actions](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions).

These checks support the bounded simulator integration milestone. They do not
establish general model reliability, prompt-injection immunity, production readiness
or real-payment readiness. A future live run still requires separate approval.
