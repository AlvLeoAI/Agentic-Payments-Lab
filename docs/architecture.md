# Phase 1 architecture

## Components and authority

| Component | Responsibility | Authority |
| --- | --- | --- |
| Operator CLI / trusted host | Create grants, register tasks, request reconciliation | Owns policy configuration and task lifecycle |
| `BuyerSession` | List, quote, request a purchase, inspect status | Bound to one application-created task |
| Catalog and `Policy` | Validate service, merchant, recipient, network, token, price, purpose, validity, integrity | Fixed trusted rules; no natural-language authorization |
| `Ledger` | Reserve funds, persist intent, claim dispatch, account for outcomes, store audit | Authoritative financial exposure |
| `Coordinator` | Sequence purchase and delivery; reconcile interrupted work | May invoke only the approved adapter path |
| `PaymentAdapter` / `SimulatedPayments` | Execute, look up, cancel the same immutable intent | Checks durable dispatch approval; simulation only |
| Mock merchant | Quote, require simulated payment, verify receipt, cache delivery | Cannot change buyer policy or budgets |

`BuyerSession.purchase` takes a typed request and quote. Its task and grant are bound
by the host; the model cannot supply purchase IDs, create tasks, edit budgets, or
choose adapter methods. Exposing these callables later does not require changing
authorization rules. Python private attributes are an encapsulation convention,
not isolation: the model must not receive arbitrary Python execution or filesystem
access in the payment process.

## Durable flow

1. The operator stores an immutable grant: allowed services, purpose, per-purchase
   cap, total budget, and validity interval. It registers a task bound to that grant.
   The application derives a purchase ID from the task ID; task uniqueness is global
   to one state directory.
2. The merchant produces a deterministic quote, stored in both its database and the
   buyer ledger. It includes the request digest and all payment terms. Quote IDs
   derive from canonical terms and simulation time. Receipt/resource digests are
   integrity identifiers, not cryptographic signatures or proof of origin.
3. In `BEGIN IMMEDIATE`, the ledger checks task binding, existing purchase identity,
   grant and quote validity, trusted catalog terms, and available budget. A conditional
   update reserves funds only if `budget - reserved - spent >= amount`.
4. A full request/quote/grant fingerprint binds the purchase permanently. Authorization
   success or denial is recorded in the same transaction as the purchase. A successful
   paid authorization stores an immutable intent with policy version `phase1-v1`.
5. A second transaction claims a reserved purchase, rechecking grant revocation and
   validity, and writes `executing` **before** calling the adapter. Only the creator
   of the purchase drives this path. Identical retries observe current state, which
   may still be in progress. They never resume or initiate a second execution.
6. The simulator verifies the exact intent against the ledger's durable dispatch
   approval. It records an attempt and an authoritative outcome transactionally in
   its own store. Unique authorization and purchase keys deduplicate adapter calls.
7. The ledger consumes authoritative settlement/failure. Settlement atomically moves
   `reserved` to `spent`; confirmed failure releases `reserved`. Exceptions and
   mismatched receipt terms become `unknown`, retaining the reservation.
8. Delivery occurs only after `settled` or `not_required`. The merchant checks the
   persisted receipt against its independent simulator authority, plus purchase,
   request, quote, amount, recipient, network, and asset binding. It durably caches
   the resource before responding. The buyer checks the resource's schema, digest,
   identifiers, request, quality, and receipt reference before marking it delivered.

A denied task is consumed. A different request or quote under the same task causes
an identity conflict rather than silently retrying. An operator may authorize a new
task explicitly. A free request follows the same policy and identity rules but
creates no payment intent or adapter execution.

## State and recovery

Payment states:

```text
new -> denied
new -> not_required                         (free service)
new -> reserved -> executing -> settled
                 |          -> failed      (authoritative failure)
                 |          -> unknown -> settled / failed / unknown
                 -> denied                 (dispatch recheck)
                 -> failed                 (atomically canceled before dispatch)
```

Delivery is separate: `not_requested -> delivered / failed`, and explicit recovery
can move `failed -> delivered`. A stale delivery failure cannot overwrite a delivered
resource. A stale payment callback cannot downgrade a terminal state or charge the
budget twice. There is no implicit refund when delivery fails.

`reconcile` records an audit event and inspects the existing state. It never calls
`execute`. A still-reserved intent is canceled in the ledger transaction, competing
atomically with dispatch. If dispatch already won, reconciliation proceeds against
the existing adapter authorization.

| Interruption / fault | Durable evidence | Recovery |
| --- | --- | --- |
| After reservation, before dispatch | Reserved funds; no dispatch marker | Atomically cancel the unsent purchase and release funds |
| After dispatch marker, before adapter | Potentially submitted payment; authority may have no record | Lookup yields unknown; retain funds; explicit local cancellation can fence a late executor |
| After adapter settlement, before ledger settlement | Simulator receipt; buyer still executing | Lookup same authorization, record settlement, deliver |
| Adapter response lost after charge | Buyer unknown; simulator settled | Lookup same authorization; no new payment |
| Adapter reports pending / lookup unavailable | Unknown exposure | Keep reserved, even after quote/grant expiration |
| After ledger settlement, before delivery | Spent funds; delivery not requested | Retrieve the resource with the existing receipt |
| After merchant persisted delivery, before buyer acknowledgment | Cached merchant resource | Retrieve identical resource and record delivery |
| Delivery fails or response is corrupt | Settled payment; failed delivery | Explicit reconciliation retries only delivery |

For simulation, `cancel` writes an authoritative cancellation tombstone in the
payment database under the same write lock as execution. A delayed executor returns
the tombstone; if payment already settled, cancellation returns that settlement.
Only this confirmation permits releasing uncertain funds. **A future wallet or x402
adapter must not claim cancellation if it cannot prove it.** It can keep returning
unknown. Revoking a grant after dispatch does not undo an already approved exposure.

Fault injection is supplied by trusted constructors/test callbacks, never merchant
HTTP parameters or buyer tools. `TIMEOUT_BEFORE_COMMIT` means before charge commitment:
it persists an unknown authorization and then raises a lost-response exception.
`TIMEOUT_AFTER_COMMIT` persists the charge before losing the response. Crash callbacks
exist after reservation, dispatch, adapter result, ledger settlement, and merchant
response. Tests also use `os._exit` to exercise actual process termination without
cleanup or shared process memory.

## Persistence decisions

SQLite runs in WAL mode with `synchronous=FULL`, 30-second connection timeout, and
per-operation connections. `BEGIN IMMEDIATE` serializes writers; no in-memory lock
is relied on for financial correctness. Schema constraints additionally enforce
nonnegative integer balances, budget bounds, unique IDs, and legal state values.
Monetary values are capped below SQLite's integer ceiling, and boolean, fractional,
and string amounts are rejected at domain parsing.

Three files represent independent failure domains without external infrastructure:

- `ledger.sqlite3`: grants, tasks, recorded quotes, purchases, accounting, events.
- `payments.sqlite3`: authorizations, deterministic outcomes, execution attempts.
- `provider.sqlite3`: merchant quotes and idempotent delivery cache.

There is intentionally no transaction spanning these files. Persist-before-dispatch
and reconciliation handle the resulting uncertainty. Same-host local disk, intact
databases, trustworthy SQLite/filesystem behavior, and retention of state are
assumptions. Network filesystems, independent database restoration, and multiple
hosts are unsupported. No schema upgrades are defined for this initial version.

Audit events are append-only through SQL triggers. They record task, authorization,
reservation, dispatch, outcome, delivery, and reconciliation history. Grants,
canonical quote/request/intent snapshots, and references reconstruct the decision.
The simulator separately records actual adapter executions. `dispatch_recorded`
means an attempt may have been sent, not proof that the adapter ran. The sanitized
export uses an explicit allowlist and excludes receipt payloads and resource text.
Purchase state and its associated events are read using one connection and one
explicit deferred read transaction. This preserves a consistent WAL snapshot while
allowing reconciliation on another connection to commit. It does not create an
atomic snapshot across the three databases.
Database rows remain sensitive application state; audit triggers do not protect
against an administrator editing files or dropping triggers.

## Dependencies and integration seams

Locked initial environment: Python 3.12; FastAPI 0.162.0, HTTPX 0.28.1, Pydantic
2.14.1; pytest 9.1.1 and Ruff 0.17.2 for development. `uv.lock` is authoritative
for these and transitive dependencies. Phase 2 adds pinned `openai-agents==0.24.0`
and `openai==3.42.1` outside this financial domain. No wallet, blockchain, or
payment-provider SDK is installed. See [the Phase 2 layer](phase2.md).

Relevant implementation documentation:

- [HTTPX ASGI transport](https://www.python-httpx.org/advanced/transports/#asgi-transport)
- [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/)
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
- [SQLite WAL](https://www.sqlite.org/wal.html)
- [uv locked environments](https://docs.astral.sh/uv/concepts/projects/sync/)

The mock merchant uses an ordinary JSON payment-required exchange, not the x402 V2
wire format. Later protocol parsing belongs behind the merchant/payment adapters;
it must produce validated internal contracts before the same policy and reservation
path can approve a purchase. The current receipt model intentionally describes a
simulation; a real adapter needs an explicit typed outcome/evidence model and its
own conformance tests. This is not a claim that real payments can be added by swapping
one class unchanged.

## Phase 2 integration boundary

The approved buyer integration wraps `BuyerSession` in four narrow tools. The host
binds subject, purpose, requested quality, task and grant; the model may request a
service and an application-issued quote handle. Read-only `context`, `constraints`
and `status_or_none` accessors avoid private internals. They add no financial
transition or dispatch path. The grant/budget summary reads one SQLite row.
Tool limits, SDK turn limits, disabled tracing, sanitized metadata and usage reporting
are implemented in `buyer_agent/`. Reports read authoritative purchase state even
after model failure. Operator-only `agent-init` writes an immutable binding witness
in the ledger before the matching agent-store copy. `agent-recover` restores only
that original binding; execution refuses missing history. Run/request checkpoints
preserve partial usage, including best-effort cancellation evidence, without changing
payment transitions. See [initialization and recovery limits](phase2.md) and the
bounded live acceptance evidence for free/paid simulation, replay and refusal.

Before any network payment phase, separately approve network/testnet, asset contract,
wallet/custody design, signing controls, facilitator trust, quote/receipt validation,
replay protection, finality, cancellation semantics, fees, refund policy, and secret
management. Phase 1 approves none of these operational choices.
