# Phase 3B.5 — scripted buyer over offline x402 fixtures

This uncommitted milestone builds on accepted Phase 3B.4 commit
`2fce3e6c9d5c9786a390d15d237a4b41bea8c264`. It connects the existing bounded
Agents SDK runner to the existing offline gate and ASGI merchant. It does not
introduce a live model call, wallet, real signature, RPC, facilitator, external
merchant transport or deployment. All protocol evidence remains synthetic.

## Explicit host selection and operator sequence

The simulator remains the default `Runtime` and every existing CLI command.
There is no new CLI backend option or model-selectable backend. Trusted Python
can construct `OfflineX402Buyer` from an existing `OfflineGateStore`, the exact
`OfflineMerchantClient`, an injected signer double, a clock and an enrolled task.
The merchant client/profile must match; its transport remains HTTPX ASGI.
These objects are trusted host capabilities, not a Python sandbox.

Use fresh, explicitly initialized offline buyer and merchant databases, and
explicit merchant/task enrollment from Phase 3B.2. Never adopt retained simulator
or live-smoke files. The adapter does not create tasks, initialize databases or
migrate state. The operator then uses:

```python
from agentic_payments_lab.buyer_agent.contracts import Goal, Settings
from agentic_payments_lab.buyer_agent.offline_x402 import OfflineX402Buyer
from agentic_payments_lab.buyer_agent.runner import run_buyer
from agentic_payments_lab.buyer_agent.store import AgentStore

# All arguments below refer to the same freshly enrolled offline fixture.
buyer = OfflineX402Buyer(buyer_store, merchant_client, signer_double, clock, task_id)
metadata = AgentStore(fresh_directory / "agent.sqlite3")
goal = Goal(subject=enrolled_subject, purpose=enrolled_purpose, quality="basic")
buyer.initialize(metadata, goal)  # Explicit enrollment, before model execution.
report = await run_buyer(buyer, metadata, goal, Settings(mode="offline"))
```

The existing closed merchant supplies only the enrolled service at basic quality.
Changed subject/purpose, unsupported quality, missing history and mismatched profiles
fail before the first model request. Current grant validity, revocation, available
budget and incidents determine eligibility; the gate rechecks spending authority
under its existing write lock. The model follows the eligible recommendation,
chooses tool calls and an issued quote handle, and produces an untrusted explanation.
It cannot choose payment terms, grant, task, purchase identity, nonce or signing domain.

The four strict input schemas remain `list_services {}`, `get_quote {service_id}`,
`purchase {quote_id}` and `get_purchase_status {}`. SDK calls remain sequential,
tracing disabled and SDK retry count zero. No administrative tool is added.
Default limits remain eight tool attempts, ten SDK turns, 512 configured output
tokens per response and a 30-second cooperative timeout. Existing byte limits
remain 32 KiB model input, 8 KiB response and 4 KiB tool output. A scripted model
does not prove provider enforcement of token settings or real-model decision quality.

A new scripted purchase normally uses five model responses and four tools.
Merchant settlement-double success leaves buyer accounting reserved and requires
explicit evidence reconciliation. After the runner finishes, the operator may
invoke `OfflineReconciler.reconcile(task_id)` and then
`OfflineDeliveryAcceptor.accept(task_id)` separately. A new buyer invocation
reports that updated durable state, normally with one status tool and two model
responses. It never triggers either operator operation.

The executable end-to-end example is
`tests/test_offline_buyer.py::test_scripted_bridge_and_separate_operator_actions`.
It checks independent SQLite execution/transfer counters at each stage and proves
that retrieval happens only in the separate explicit acceptance operation.
Run it inside the same OS network isolation used by the full validator.

## Ownership and durable bindings

| State | Owner and rule |
| --- | --- |
| Task, request, purchase, grant, reservation, claim, nonce, dispatch fence | Existing buyer database/gate; unchanged financial transitions |
| Original agent intent | Immutable `agent_bindings` row in that buyer file: context, original goal, backend, policy/request/requirements hashes |
| Agent binding copy, quote handles, runs, usage and tool events | Existing separate agent metadata database; no financial authority |
| Settlement-double claim, identity/nonce tombstones and cached content | Existing separate merchant database; cannot settle buyer accounting |
| Accepted fixture evidence, accounting, delivery acceptance and incidents | Existing explicit operator APIs and atomic buyer events |

No new database, table or schema migration is added. Buyer version 4 and its
existing migration rules remain unchanged. Simulator binding serialization remains
byte-compatible; the additional scope exists only for this explicitly selected adapter.

Initialization commits the independent buyer witness first, then the agent copy.
There is no cross-file transaction. If the second commit fails, execution refuses
the missing copy. Missing/changed metadata cannot silently rebind an existing task.
`buyer.recover(metadata)` is an explicit operator operation: it validates and copies
only the original witness, without caller-supplied replacement intent. It marks
history incomplete and cannot reconstruct quotes, usage, events or old runs.
If the independent witness is missing/inconsistent, recovery refuses; preserve the
surviving files for operator investigation. Already claimed/canceled purchases and
legacy unscoped bindings cannot be adopted as a first agent enrollment.

An unpaid ASGI challenge is strictly validated against the closed policy. Durable
quote handles bind that exact requirements hash, policy, original request, task,
grant and purchase, with issuance/expiry timestamps. Only hashes and normalized
public terms are stored, never merchant headers or signatures. At purchase, the
adapter reconstructs the one permitted **unsigned requirements** envelope from the
same approved policy. This is not reconstruction of a signature or authorization.
The gate alone creates authorization/nonce ownership and atomically reserves budget.

Fabricated, cross-task, cross-instance/backend and changed-profile handles reject
before execution. An expired quote cannot start a payment. A previously recorded
attempt or cancellation returns its existing status without entering the gate,
even when the original handle has since expired. Concurrent first callers can reach
the gate, whose existing durable single-attempt claim admits at most one signer
and paid dispatch. Missing transient signatures are never regenerated or resent.

## Reports, interruption and recovery

`mode=offline` and `model_usage.source=scripted` describe model execution. The
purchase instead has `payment_backend=offline_x402`, `evidence_source=offline_fixture`
and `blockchain_settlement_verified=false`. Payment state retains the actual gate
phase, including `fixture_settled`, `fixture_unused` and uncertainty; there is no
invented `SimulatedReceipt` or mapping to verified real settlement. Accepted delivery
is represented by a digest, not merchant content. The simulator report shape is
unchanged. Model-generated claims cannot overwrite authoritative purchase fields.

The incident flag stays visible independently of accepted payment/delivery history.
Finality incidents fence new claims/dispatch and delivery acceptance through the
existing authorities; they never reverse accepted accounting. A delivery failure
cannot release payment exposure or trigger repayment. Status/checkpoints/finalization
use read-only financial snapshots, with a short connection lock wait during cleanup.
They do not reopen/migrate stores, observe evidence, retrieve bodies or repair caches.

| Interruption | Durable consequence / explicit next action |
| --- | --- |
| Quote persistence fails or quote response is lost | No payment side effect; inspect metadata; a new quote cannot replace a recorded claim |
| Process exits after claim, before signer | Original reservation/ownership survives; no new signature on another agent run |
| Signer completes after cancellation | Gate fences dispatch; exposure remains reserved pending explicit evidence |
| Dispatch/settlement succeeds but response or agent event is lost | Preserve original authorization and reserved exposure; no resend or automatic reconciliation |
| Model fails/cancels after explicit fixture settlement/delivery | Agent termination changes; authoritative financial/delivery history does not |
| Commit succeeds but final report is lost | Inspect the existing buyer state and run record; a later agent invocation reads it |

A durable run record precedes the first model call. Requests and observed usage
are checkpointed incrementally. Pending requests remain explicitly unresolved;
known partial token totals are retained. Zero observed tokens with an unresolved
request does not mean zero consumption. These scripted usage fixtures are not
billing estimates. `run_complete`, usage completeness and authoritative financial
state answer different questions. Runs are per invocation; purchase history and
claims persist across runs. Recovered metadata is explicitly incomplete history.

Cancellation propagates after bounded best-effort run finalization. The bridge
checks cancellation after a model/challenge returns late and before entering the
gate, preventing a swallowed cancellation from starting a new claim. Cancellation
during signing retains the existing gate's conservative fencing/uncertainty behavior;
the agent adds no financial cancellation, release or compensation operation.
Abrupt exit, repeated cancellation, I/O stalls and persistence failures can defeat
cleanup. Timeouts remain cooperative, not hard process or dollar limits. Restoring
an older complete database can erase witnesses/claims/incidents and is still outside
the guarantee. No general backup, automatic recovery or global tombstone service exists.

## Validation and limits

Focused tests cover scripted SDK flow and unchanged schemas; explicit operator
reconciliation/acceptance; binding/quote isolation; forged success and tool attacks;
100 overlapping calls and four separate processes; partial usage/cancellation,
late responses, metadata failures, lost acknowledgements and real process exits.
Counts come from independent SQLite signer/dispatcher/settler instrumentation and
synthetic transfer records, not the hardcoded simulator signing statistic.

Run `scripts/validate_offline.py` using the fail-closed namespace launcher in
`AGENTS.md`, after locked dependency installation. It includes subprocess isolation,
lint/format/lock checks, the complete test suite and the unchanged repeated
simulator plus free/paid/unaffordable offline agent demos. Validation evidence and
source hashes are retained outside the repository for independent review. Hosted
CI for this uncommitted milestone has not run.

Live integration remains blocked by the unresolved local SQLite WAL-reset fix
prerequisite and the separately scoped signer interoperability, external merchant,
RPC/ABI evidence decoding and authoritative finalized-chain observation work.
A future live-model pilot needs separate authorization; its named model remains
`gpt-6-luna`, with no automatic fallback. This scripted slice establishes no
live-model quality, real payment, blockchain finality or complete Phase 3B/3C acceptance.
