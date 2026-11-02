# Agentic Payments Lab

A deterministic, fully offline procurement engine that separates purchase decisions,
financial authorization, payment execution, and resource delivery.

**Phase 1 is implemented. All payments and receipts are simulated.** There are no
wallets, private keys, blockchain RPCs, payment facilitators, OpenAI calls, or live
x402 integrations. `SIM-USDC` uses six decimal places for arithmetic exercises; it
is not USDC and has no monetary value. The only network identifier is
`simulation:local`.

The long-term project is an OpenAI Buyer Agent using Agents SDK / Responses and a
restricted x402 V2 payment adapter. Those integrations require a separately approved
phase. No alternative agent orchestration framework is used.

## Quick start

Prerequisites: Python 3.12 and [uv](https://docs.astral.sh/uv/). Development was
validated with Python 3.12.3 and uv 0.11.7. The committed `uv.lock` fixes package
versions; `pyproject.toml` defines compatibility bounds.

```bash
uv sync --locked
uv run --offline --no-sync payments-lab demo
```

Dependency installation can use the internet. After installation, all application
commands and tests run offline without credentials or environment variables.
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

```bash
uv run --offline --no-sync pytest -q
uv run --offline --no-sync ruff check .
uv run --offline --no-sync ruff format --check .
uv lock --check --offline
```

To run the complete validation under OS-level network isolation on Linux, install
dependencies first, then run:

```bash
uv sync --locked
parent_netns=$(readlink /proc/self/ns/net)
unshare --user --map-root-user --net -- \
  .venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

The validator requires a new network namespace containing only loopback and checks
that a child process inherits it. It then runs lockfile validation, Ruff, the full
test suite, and two identical CLI demos. It checks the payment authority's SQLite
records for one execution and one charge after both demos. Caches and simulation
data are kept in a temporary directory outside the repository. Isolation setup or
verification failure stops validation; there is no unrestricted fallback.

The suite covers authorization tampering, expiration, budget limits, forged receipts,
delivery recovery, 100 simultaneous identical retries, 20 competing purchases,
real subprocess termination/restart at five durable boundaries, and audit snapshot
consistency during reconciliation. Socket/DNS patches in tests provide additional
checks but do not establish OS-level isolation. Likewise, `uv --offline` restricts
dependency access, not application networking.

CI targets Ubuntu 24.04. After dependency installation, it uses
`sudo --non-interactive unshare --net` to run this same validator, including all test
and demo subprocesses, in an isolated network namespace. CI requires passwordless
sudo and network namespace support, avoiding reliance on unprivileged user namespace
policy. The local command above requires unprivileged user namespaces. A CI workflow
in the repository does not imply a hosted run has already succeeded; check GitHub
Actions after publishing an approved commit.

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
│   └── threat-model.md
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

## Limits and next phase

This is a local single-operator system, not a production payment service. SQLite
uniqueness and atomic transactions demonstrate at most one durable simulated charge
per purchase **within retained, consistent local state**. They do not prove universal
exactly-once payment or real blockchain finality. Python objects and local database
permissions are trusted; the process is not a sandbox and audit triggers are not
tamper-proof against a filesystem administrator.

There is no authentication, encryption at rest, schema migration system, background
reconciler, automated refund, live wall-clock service, or distributed deployment.
An uncertain outcome may remain reserved indefinitely until authoritative
reconciliation. Availability is deliberately sacrificed when the result is unknown.

See [architecture and recovery decisions](docs/architecture.md) and the
[threat model](docs/threat-model.md). The proposed Phase 2 is a separately approved
OpenAI Agents SDK / Responses integration against this simulator, with bounded
tools and adversarial evaluations. Real x402, wallet custody, signing policy,
facilitator selection, and Base testnet work remain later decisions.
