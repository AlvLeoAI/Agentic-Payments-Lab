# Agentic Payments Lab

A deterministic procurement simulator that separates purchase decisions,
financial authorization, payment execution, and resource delivery.

**Phase 1 is accepted; Phase 2 adds a bounded OpenAI SDK buyer. All payments and
receipts remain simulated.** The buyer defaults to a credential-free scripted
model through the actual SDK runner. Explicit live Responses acceptance checks passed
for free acquisition, simulated paid acquisition, replay, and unaffordable refusal.
These are bounded integration checks, not evidence of general model reliability.
There are no wallets,
private keys, blockchain RPCs, payment facilitators, or live x402 integrations.
`SIM-USDC` uses six decimal places for arithmetic exercises; it
is not USDC and has no monetary value. Simulator tasks use `simulation:local`.

**Phase 3B.1 adds an offline x402 validation/gate module**, exercised only with
signer and dispatcher doubles in fresh, separately enrolled state. Its Base Sepolia
USDC identifiers describe protocol fixtures; it cannot sign or send a payment.
The original slice is separate from the agent/CLI. See [scope, evidence and recovery](docs/phase3b1.md),
including the unresolved SQLite WAL-reset patch prerequisite for live use.

**Phase 3B.2 adds offline merchant integration** through FastAPI and HTTPX ASGI:
explicit host enrollment, the required payment-identifier extension, durable
request/nonce binding, one settlement-double invocation per committed claim, and
non-charging cached retrieval. Pending or interrupted attempts never retry settlement.
Merchant-reported success does not settle buyer accounting. There is no agent/CLI
backend switch, real signer or facilitator connection. See the
[offline merchant contract and failure matrix](docs/phase3b2.md).

**Phase 3B.3 adds offline evidence validation and explicit reconciliation** over
bounded synthetic public records. Matching finalized fixture receipts/events or
post-expiry non-use can resolve the original buyer reservation exactly once.
Missing, malformed or provisional evidence stays inconclusive; a contradiction
after accepted finality records an operator incident without reversing accounting.
This is a fixture-only Python operator API, with no RPC client, real proof, startup
worker or agent/CLI integration. See [evidence, recovery and limits](docs/phase3b3.md).

**Phase 3B.4 adds explicit offline buyer delivery acceptance** for an already
fixture-settled purchase. A non-charging ASGI retrieval validates the original
request, purchase identity and deterministic content before recording one durable
acceptance in the buyer audit. Repeated calls, concurrent callers and restarts
cannot duplicate acceptance or execute another payment. Delivery failure preserves
settled accounting; finality incidents fence new acceptance. This milestone is
published at `2fce3e6`. See [delivery acceptance and limits](docs/phase3b4.md).

**Phase 3B.5 connects the bounded buyer to offline x402 fixtures**, through an
explicit host Python adapter and the deterministic scripted model. The same four
tools use durable task/profile/request-bound quotes and the original single-attempt
gate. The simulator remains the CLI default. Fixture reconciliation and delivery
acceptance stay separate operator actions; agent status and retries perform neither.
Published with its reviewed concurrency correction at
[`6198c97`](https://github.com/AlvLeoAI/Agentic-Payments-Lab/commit/6198c97d5ad4e30ae9b1dff8380cbfdc8d11f128);
the [exact-SHA hosted run](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions/runs/42416229737)
passed 657 tests, isolation/subprocess checks and repeated demos. See the
[offline buyer contract, recovery and validation](docs/phase3b5.md).

**Phase 3B.6 adds offline evaluation and call-budget preparation**, published at
[`bcaa94c`](https://github.com/AlvLeoAI/Agentic-Payments-Lab/commit/bcaa94c0c2a8baa4abb673f3db25ac58450f0832)
with [708 passing hosted tests](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions/runs/42424403222).
Its evaluator scores real fixture ledger/authority records and tool
traces. A separate simulator suite exercises the pinned Responses wire path through
an in-memory transport, with complete serialized-body bounds and durable planning
reservations. It makes no API calls and does not enable live models on the x402
buyer. See [evaluation, accounting and remaining prerequisites](docs/phase3b6.md).
The [versioned offline evaluation catalog](evals/README.md) maps its seven existing
cases into two linked sequences and validates their metadata against actual evaluator
check identifiers. The catalog describes coverage; it does not drive execution.

A separate [bounded Luna simulator evaluation campaign](docs/luna-evaluation.md)
freezes eight cases across three trials with one durable request/cost envelope.
One separately authorized campaign completed all 24 entries on 2026-11-09
(America/Argentina/Cordoba): 82 known complete Responses requests, estimated
USD 0.0066626 (not verified billing), six new simulated charges and zero replay
charges. It added no unresolved API reservation and preserved seeded unknown
payment exposure. Deterministic safety/accounting checks passed; human explanation
review remains pending. See the [sanitized baseline and four review examples](docs/luna-baseline.md).
This exploratory sample does not establish general reliability or injection
resistance. The historical USD 0.001239 unresolved API reservation and USD 0.0007724
pilot estimate remain separate and unchanged. No further live calls or live models
on x402 are authorized.

**Phase 3B.7 prepares a guarded simulator pilot offline**, with a checksum-pinned
Python 3.12.16 / SQLite 3.53.1 runtime, sourced Luna pricing and cache-write lifetime
accounting. Its supported fresh-state initializer checks the actual runtime before
opening durable state. Responses still use in-memory mocks; live activation is
rejected. The existing `.venv`/system SQLite remains unpatched. This preparation
was published at
[`4580e76`](https://github.com/AlvLeoAI/Agentic-Payments-Lab/commit/4580e764cdac9925def8510be2e845c611de9af8)
with [770 passing hosted tests](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions/runs/42434354806).
See [runtime provenance and usage](docs/phase3b7.md).

**Phase 3B.8 adds guarded simulator live-pilot activation.** Offline validation
covers the shared request/cost envelope, exact routing, serialized policy and
incremental raw usage. A separately authorized Luna pilot completed purchase,
same-purchase replay and unaffordable refusal on 2026-11-08: ten new requests,
one simulated charge and no duplicate execution. Its USD 0.0007724 usage-based
estimate is separate from the original stopped request's unchanged USD 0.001239
reservation. No further live calls are authorized by this milestone. See the
[sanitized pilot evidence, activation boundaries and limitations](docs/phase3b8.md).

The buyer uses the official Python Agents SDK with Responses and four bounded
tools. Real payment integrations require another approved phase. No alternative
agent orchestration framework is used.

## Quick start

Prerequisites: Python 3.12 and [uv](https://docs.astral.sh/uv/). Development was
validated with Python 3.12.3 and uv 0.11.7. The committed `uv.lock` fixes package
versions; `pyproject.toml` defines compatibility bounds.

```bash
uv sync --locked
uv run --offline --no-sync payments-lab demo
```

Dependency installation can use the internet. After installation, the Phase 1
commands, offline buyer, and all tests run without credentials or networking.
HTTPX invokes the FastAPI merchant through its in-process ASGI transport. No web
server, port, Docker daemon, or network connection is required.

The demo creates an operator grant of 40,000 atomic units, buys a report for 10,000
(`0.010000` SIM-USDC), and rejects a forged merchant quote. On a fresh state
directory its output includes:

```json
{
  "adapter": {
    "charged_atomic": 10000,
    "execution_attempts": 1,
    "logical_charges": 1,
    "signing_attempts": 0
  },
  "budget": {"available": 30000, "limit": 40000, "reserved": 0, "spent": 10000}
}
```

The paid result is `settled` / `delivered`; the unauthorized result is `denied` /
`not_requested`. Repeating the command against the same state returns the same
results without another charge. To run an independent demo, choose another
directory with `payments-lab --state-dir /tmp/my-payment-demo demo`.

## Historical pilot viewer

The local read-only Streamlit page shows the saved Phase 3B.8 purchase, replay and
refusal results. It reads one sanitized bundled snapshot, with separate payment
and delivery states, per-scenario charge deltas and the original unresolved API
reservation. It does not open the original databases or execute agent/payment work.

After installing the verified runtime and locked dependencies described below:

```bash
.runtime/venv/bin/python -I -B scripts/view_pilot.py
```

Open `http://127.0.0.1:8501`; stop with Ctrl+C. The supported launcher isolates the
child's environment and configuration, including script-adjacent Streamlit files.
It binds only to loopback, preserves CORS/XSRF and disables telemetry. Loopback
binding is not an OS egress sandbox. See [viewer data, launch and validation boundaries](docs/pilot-viewer.md).
No credentials, uploads or execution controls are required or provided.

## Bounded buyer (Phase 2)

The operator creates a grant/task before running the agent. The following commands
exercise the actual SDK runner with the deterministic scripted model:

```bash
uv run --offline --no-sync payments-lab --state-dir .lab/buyer init --budget 10000 --cap 10000
uv run --offline --no-sync payments-lab --state-dir .lab/buyer task buyer-1
uv run --offline --no-sync payments-lab --state-dir .lab/buyer agent-init buyer-1 \
  --subject offline-report --purpose research --quality standard
uv run --offline --no-sync payments-lab --state-dir .lab/buyer agent buyer-1 \
  --mode offline --subject offline-report --purpose research --quality standard
```

The explicit `agent-init` operation binds subject, purpose and quality before any
model request. Repeat the last command to inspect the same purchase without another
charge. For another request, the operator must create a new task. `basic` selects
the free report; `premium` with this grant
reports no suitable affordable service. The agent cannot lower requested quality,
create authority, reconcile, cancel, or replace an uncertain purchase.

The report separates application-owned `purchase`/`decision` from untrusted
`model_explanation`; API usage is separate from simulated spending. See
[Phase 2 architecture, limits and live smoke evidence](docs/phase2.md).

## Inspect a purchase

```bash
uv run --offline --no-sync payments-lab list
uv run --offline --no-sync payments-lab status demo-paid
uv run --offline --no-sync payments-lab audit demo-paid
uv run --offline --no-sync payments-lab audit demo-denied
uv run --offline --no-sync payments-lab reconcile demo-paid
```

`audit` writes sanitized JSON to stdout; redirect it to a file if needed. It omits
receipt payloads, raw requests, and delivered content. `status` explicitly shows
the purchased resource and a simulated receipt **identifier**, not the full receipt.
Each audit export reads purchase state and events from one SQLite snapshot, including
when reconciliation commits while the export is being read.

For a manual workflow, use a separate directory:

```bash
uv run --offline --no-sync payments-lab --state-dir .lab/manual init
uv run --offline --no-sync payments-lab --state-dir .lab/manual task research-1
uv run --offline --no-sync payments-lab --state-dir .lab/manual quote
# Copy the quote_id from the preceding output:
uv run --offline --no-sync payments-lab --state-dir .lab/manual purchase research-1 --quote-id QUOTE_ID
uv run --offline --no-sync payments-lab --state-dir .lab/manual status research-1
```

The default request is `report-standard`, subject `offline-demo`, purpose `research`.
`quote` and `purchase` accept `--service`, `--subject`, and `--purpose`; use identical
values for both. A quote is a recorded offer from the approved local catalog, not
spending authorization. Authorization occurs atomically during purchase.

`init` and `task` are **operator commands**. The future agent interface must never
expose them. `init` supports `--grant-id`, `--budget`, and `--cap` in atomic units;
`task` binds a logical task to a grant. The application computes the purchase ID.
An existing task cannot change grant or purchase contents, even after rejection.
The operator must explicitly create a new task to authorize a different attempt.

Time is deterministic: the default simulation clock is `1767225600` (2026-01-01
UTC), not the wall clock. Set global `--now UNIX_SECONDS` to advance a scenario.
Quotes last 60 simulation seconds and CLI grants last 3,600. Advancing time does
not release uncertain payments. Reusing an existing grant ID with different
contents is an error, so use a new state directory for a new timed scenario.

## Implemented behavior

- Three fixed-price report services: free/basic (0), standard (10,000), and premium
  (30,000 atomic units), with request/response schemas and approved payment terms.
- Immutable operator grants and application-owned purchase IDs, one purchase per
  task, request/quote fingerprints, strict integer money, and policy versioning.
- SQLite transactions for authorization, reservation, dispatch, settlement, and
  append-only audit events. Unique purchase and authorization keys prevent duplicates.
- Independent payment and delivery states; failed delivery cannot refund a payment
  implicitly or trigger a new charge.
- Simulated success, decline, lost response, unknown outcome, merchant failure,
  corrupted response, and interruption checkpoints.
- Explicit reconciliation of the existing authorization, including safe local
  cancellation and recovery of already purchased resources.
- A bounded `BuyerSession` interface for future agent tools; no signing, grant
  creation, task creation, or arbitrary URL input in that interface.

## Validation

The full suite now requires the separately installed, verified Linux x86_64 runtime.
The original `.venv` remains available for legacy simulator use; these commands do
not replace it or open retained state. Installation may access package sources:

```bash
python3 scripts/prepare_runtime.py
UV_PROJECT_ENVIRONMENT=.runtime/venv uv sync --locked --no-python-downloads \
  --python .runtime/cpython-3.12.16-20261026/bin/python3.12
export UV_PROJECT_ENVIRONMENT=.runtime/venv
uv run --offline --no-sync pytest -q
uv run --offline --no-sync ruff check .
uv run --offline --no-sync ruff format --check .
uv lock --check --offline
```

To run the complete validation under OS-level network isolation on Linux, install
dependencies first, then run:

```bash
parent_netns=$(readlink /proc/self/ns/net)
unshare --user --map-root-user --net -- \
  .runtime/venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

The validator requires a new network namespace containing only loopback and checks
that a child process inherits it. It then runs lockfile validation, Ruff, the full
test suite, two identical Phase 1 demos, and repeated offline agent scenarios for
free, paid and unaffordable requests. It checks the payment authority's SQLite
records directly for expected execution/charge counts. Caches and simulation
data are kept in a temporary directory outside the repository. Isolation setup or
verification failure stops validation; there is no unrestricted fallback.
The viewer's AppTest cases also run in this suite. A final real-server smoke checks
loopback binding, health/HTML serving, clean shutdown and that nine synthetic
configuration/secret/environment files in the original discovery roots were never
opened. That smoke enables loopback only inside the verified disposable namespace;
it does not render JavaScript. Browser rendering is a separate local check.

The suite covers authorization tampering, expiration, budget limits, forged receipts,
delivery recovery, 100 simultaneous identical retries, 20 competing purchases,
real subprocess termination/restart at five durable boundaries, and audit snapshot
consistency during reconciliation. Phase 2 tests cover narrow tool schemas, scoped
quote handles, limits, malicious scripted output, and mocked Responses requests.
Socket/DNS patches in tests provide additional
checks but do not establish OS-level isolation. Likewise, `uv --offline` restricts
dependency access, not application networking.

CI targets Ubuntu 24.04. The configuration installs the same checksum-pinned
runtime and locked dependencies before isolation, with a 20-minute whole-job
timeout. The timeout was increased to accommodate the expanded local validator
and setup; no validation step or isolation check was removed. Hosted CI has not
run for this uncommitted catalog/campaign/closure candidate. Local validation is
not hosted proof. CI uses
`sudo --non-interactive unshare --net` to run this same validator, including all test
and demo subprocesses, in an isolated network namespace. CI requires passwordless
sudo and network namespace support, avoiding reliance on unprivileged user namespace
policy. The [Phase 3B.7 hosted run](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions/runs/42434354806)
for exact commit `4580e764cdac9925def8510be2e845c611de9af8` succeeded: 770 tests,
verified namespace/subprocess isolation, repeated simulator and agent demos, and
offline evaluation. That result covers the Phase 3B.7 baseline; each later commit
requires its own exact-SHA hosted result. Hosted runtime verification does not patch
the operator's old `.venv` or system SQLite. The local command above requires
unprivileged user namespaces.

## Repository structure

```text
.
├── AGENTS.md
├── LICENSE                       # Original MIT license
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example                  # No credentials needed
├── .gitignore
├── .github/workflows/ci.yml
├── docs/
│   ├── architecture.md
│   ├── threat-model.md
│   └── phase2.md
├── scripts/
│   └── validate_offline.py        # Fail-closed network checks and validation
├── src/agentic_payments_lab/
│   ├── domain.py                 # Typed, immutable contracts
│   ├── catalog.py                # Operator-approved services
│   ├── policy.py                 # Pure authorization checks
│   ├── ledger.py                 # Durable financial state and audit
│   ├── payments.py               # Payment protocol and local simulator
│   ├── provider.py               # FastAPI merchant and ASGI HTTPX client
│   ├── coordinator.py            # Purchase, reconciliation, buyer surface
│   ├── storage.py                # SQLite connection/transaction helpers
│   ├── clock.py                  # Injectable deterministic clock
│   ├── runtime.py                # Local component composition
│   ├── buyer_agent/              # SDK runner, tool boundary, agent metadata, scripts
│   ├── cli.py
│   ├── __main__.py
│   └── __init__.py
└── tests/                        # Policy, HTTP, concurrency, CLI, recovery
```

Runtime state lives in ignored `.lab/`: `ledger.sqlite3`, `payments.sqlite3`, and
`provider.sqlite3`, plus SQLite WAL files when active. Treat these as one simulation
instance. Deleting only one database while retaining the others breaks recovery
assumptions. For a fresh scenario use a new directory. Keep all three stores and
their WAL state when preserving evidence; a safe backup procedure is future work.
Agent commands additionally create ignored `agent.sqlite3` for immutable request
bindings, task-scoped quote handles and sanitized run events. It has no financial
authority; preserve it with the three simulator stores when retaining agent history.
The buyer ledger independently retains the original agent binding. Missing metadata
blocks agent execution; explicit `agent-recover TASK` restores only that original
binding, never lost run/usage history. See [the recovery contract](docs/phase2.md).

Run records persist pending requests and observed token totals incrementally. Agent
cancellation is independent of payment/delivery state and propagates after best-effort
cleanup. Unknown usage is marked incomplete; a hard crash may leave a running record.

## Limits and next phase

This is a local single-operator system, not a production payment service. SQLite
uniqueness and atomic transactions demonstrate at most one durable simulated charge
per purchase **within retained, consistent local state**. They do not prove universal
exactly-once payment or real blockchain finality. Python objects and local database
permissions are trusted; the process is not a sandbox and audit triggers are not
tamper-proof against a filesystem administrator.

There is no authentication, encryption at rest, general migration framework, background
reconciler, automated refund, live wall-clock service, or distributed deployment.
The buyer file has narrow additive upgrades from the exact Phase 2 and initial
Phase 3B.1 schemas; version 2 adds durable cancellation before an offline claim.
Version 3 preserves those tables and rejects older binaries on reopen to protect the
[offline reconciliation incident fence](docs/phase3b3.md#ownership-persistence-and-reporting).
Unknown/incomplete schemas are rejected before persistent journal changes.
Use fresh development state and review the
[upgrade procedure](docs/phase3b1.md#persistence-and-upgrade) before opening retained state.
An uncertain outcome may remain reserved indefinitely until authoritative
reconciliation. Availability is deliberately sacrificed when the result is unknown.

See [architecture and recovery decisions](docs/architecture.md) and the
[threat model](docs/threat-model.md). Phase 2 offline validation demonstrates runner
and boundary behavior, not real-model reliability or prompt-injection resistance.
Real x402, wallet custody, signing policy,
facilitator selection, and Base testnet work remain later decisions.
