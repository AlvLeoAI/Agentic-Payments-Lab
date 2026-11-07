# Phase 3B.4 — explicit offline buyer delivery acceptance

This milestone builds on published Phase 3B.3 commit
`00abf23ef55551e35bf2548bb731a670cd868904`. It adds an explicit Python operator
operation for accepting the existing deterministic merchant fixture. It remains
offline, with injected execution/settlement doubles and HTTPX ASGI transport.
It does not complete Phase 3B or authorize Phase 3C, a wallet, real signing,
OpenAI/RPC/facilitator calls, external HTTP transport or deployment.

## Operator contract

After explicit Phase 3B.3 evidence reconciliation has produced `fixture_settled`
for the original enrolled task, trusted host code may invoke:

```python
from agentic_payments_lab.x402_delivery import OfflineDeliveryAcceptor
from agentic_payments_lab.x402_provider import OfflineMerchantClient

report = await OfflineDeliveryAcceptor(
    existing_buyer_store,
    OfflineMerchantClient(existing_offline_merchant),
    clock,
).accept("original-task")
```

The supplied clock returns an integer timestamp. This operation is not exposed as
an agent tool, CLI command, startup hook, polling loop or cleanup callback. The
four agent tools, simulator defaults and deterministic eligibility are unchanged.
It does not enroll a task, create an authorization, reconstruct signatures,
reconcile payment, repair a merchant cache, dispatch or settle anything.

The exact closed `OfflineMerchantClient` and matching operator policy are required.
The client retains the existing fixed status route and in-process host capability;
it cannot follow merchant-supplied URLs or redirects. The ASGI application and
operator-supplied Python objects are trusted host code, not an execution sandbox.

One invocation makes at most **one non-charging status GET**. An already accepted
purchase returns its durable status with zero retrievals, including after restart,
cache expiration or a subsequent incident. Concurrent invocations may each retrieve
once before one commits; they still produce only one acceptance record. There are
no automatic retries. A later explicit operator invocation may retry retrieval,
which is separate from payment execution and never resends payment.

## Validation and authority

The buyer first reads its original task, request, grant, purchase, authorization,
terms, nonce and accepted transfer evidence. Pending, unknown, unused, missing or
inconsistent buyer payment history rejects before retrieval. Merchant success,
cached delivery and a transaction identifier cannot establish buyer settlement.
Expired or revoked grants do not prevent accepting an already settled delivery;
this does not authorize new spending.

The returned HTTP envelope must be JSON and match its status: cached/200,
gone/410, or pending/not-ready/202. Strict models reject coercions, extra or
duplicate fields, malformed JSON, oversized bodies and inconsistent state. The
report must remain explicitly offline. Purchase and merchant payment identifiers,
service, subject, request digest, resource digest and envelope digest are checked
against the original buyer binding. The existing fixture contract requires basic
quality, no receipt ID and the exact deterministic summary for that subject.
Recomputing a hash around altered content does not make it acceptable. This does
not claim validation of arbitrary generated content, provenance or useful quality.

The **16 KiB limit applies to parsing and acceptance after HTTPX/ASGI has buffered
`response.content`**. It does not bound transport allocation, response generation
or memory consumed by the in-process merchant. No streaming or external transport
has been added. The timeout defaults to five seconds, configurable from one to
thirty. It is cooperative: blocking Python, an uncooperative ASGI application or
filesystem I/O can outlast it. The existing SQLite lock wait remains 0.2 seconds;
it is not a bound on total filesystem execution.

The original binding and incident latch are checked again under the buyer write
lock after retrieval. An incident that commits first blocks a new acceptance.
An acceptance that commits first remains accepted when an incident arrives later;
the incident is reported independently and payment accounting is never reversed.

## Durable state and reports

No new table or database is added. The append-only `offline_delivery_accepted`
audit event **is** the durable buyer acceptance. Its insertion and duplicate check
share one `BEGIN IMMEDIATE` transaction, so no acceptance/audit dual write exists.
It records the binding, authorization, request, terms, resource and envelope
digests, fixed offline source/quality and timestamp. Merchant prose, response
bodies, headers, signatures and exception messages are not persisted or returned
by the acceptor. Digests are not secret and do not prove blockchain settlement.

Only the existing buyer file is written. Merchant rows and settlement-authority
records remain unchanged; there is no cross-database transaction or atomicity
claim. No SQLite transaction is held across the ASGI await. Reservations, spent
amounts, original authorization and accepted payment evidence are unchanged by
delivery acceptance or failure.

The report reads financial state, delivery events, reconciliation and ordered
audit in one explicit read snapshot. `delivery_state` is `not_requested`, `failed`
or `delivered`; an accepted result cannot be downgraded by a later failed call.
The `delivery` section contains the accepted digests and last distinct persisted
failure. `blockchain_settlement_verified` remains false.

`delivery.last_failure` means the **last distinct persisted failure**, not the
latest invocation. Identical binding/reason failures are deduplicated. For
gone → timeout → reopen → gone, the last failure remains timeout while the current
result says gone, with two failure audit records. Successful acceptance preserves
earlier failure history. This is not a durable invocation counter.

`accept()` separately returns `current_invocation` with `outcome`
(`accepted`, `already_accepted` or `failed`), a fixed reason or null, and that
invocation's retrieval attempts (zero or one). Another caller may commit between
the write decision and the report snapshot; the two sections retain their own
meanings. A standalone store report has no current-invocation claim. Transient
results may be lost even when acceptance committed.

The buyer retains acceptance digests, not a second copy of the content. If the
merchant cache later disappears, the audit proves the earlier fixture acceptance
but cannot reconstruct or return that content. Delivery acceptance is not a new
backup or long-term content archive.

## Failure and recovery contract

| Boundary | Durable result and explicit next action |
| --- | --- |
| Missing task, unproven/unused payment or incident before retrieval | Reject; no GET, new identity, payment or automatic recovery |
| Invalid/cross-task body, pending/gone cache, unavailable response or timeout | Fixed deduplicated failure when persistence succeeds; original finance unchanged |
| Cancellation before response, after response or late swallowed cancellation | Fresh sanitized cancellation; no acceptance or compensating action before commit |
| Incident commits while retrieval is outstanding | Reject acceptance under the buyer write lock; keep the incident and original finance |
| Exit before acceptance write, after insertion or before commit | No committed acceptance; explicit reopen and retrieval may accept existing cached content |
| Commit succeeds; cancellation, process exit or lost report follows | Acceptance/audit survives; explicit repeat returns accepted with zero retrievals |
| Concurrent threads/tasks/processes | Serialized acceptance check and event insertion; one acceptance, no additional execution or transfer |
| Acceptance commits before an incident | Preserve accepted delivery and accounting, expose the incident; no automatic rollback |
| Persistence failure | Return fixed unavailable error; inspect original state because commit may already have succeeded |

Cancellation propagates; there is no best-effort delivery cleanup write to promise.
Abrupt process termination, repeated cancellation, persistence errors or I/O stalls
can prevent any result or failure record. Python tracebacks/debuggers/process
memory can retain locals; sanitized errors and transient references are not secure
memory erasure. The caller must not turn uncertainty into a new payment.

## Schema compatibility

Buyer version **4** uses the same physical tables as versions 2 and 3, with new
append-only delivery event semantics. Exact schema/profile checks precede changes
and repeat under the transaction lock; the version marker commits atomically.
Published version-3 openers reject version 4 rather than ignore accepted delivery.
Missing or unsupported history fails closed. Merchant schema remains version 1.

Stop old processes before opening a database with the new code. This is a reopen
compatibility fence, not a hot upgrade or protection from already-running old
writers. An interrupted version-3 upgrade leaves version 3 or complete version 4
with original historical rows. Explicit reopen can finish the marker/WAL setup
without execution or delivery replay. Tests use a SQL fixture generated from the
accepted Phase 3B.3 implementation with settled and reserved purchases, cancellation,
observations and an incident. Only disposable fixture databases are migrated.

Concurrent openers exposed a pre-existing window after the migration commit:
another connection can hold a lock while `PRAGMA journal_mode=WAL` returns
`SQLITE_BUSY` immediately. The opener now retries only that idempotent post-commit
journal setup, with fresh connections and the existing timeout as its retry
deadline. Migration/commit, financial writes and execution are never replayed.
Persistent contention and non-lock errors still propagate. The deadline is
cooperative around SQLite/filesystem calls, not a hard total I/O time bound.
Unsupported-schema preflight and under-lock validation remain unchanged.

Restoration of an older complete database can lose acceptance or incident history
and remains outside the guarantee. Preserve surviving buyer/merchant state and
inspect it explicitly; do not reconstruct missing history from new caller input.

## Validation and remaining work

The new tests exercise deterministic content/identity checks; malformed HTTP/JSON;
replay, 100 overlapping callers and four separate processes; both incident orders;
cancellation/late response and timeout; write/commit/report failures; abrupt exits;
read-snapshot consistency; and version-3 migration interruptions/concurrent openers.
Counters come from signer/dispatcher instrumentation and separate authority SQL
tables, never the hardcoded simulator signing statistic. They are synthetic call
and transfer counts, not evidence of real blockchain charges.

Run the existing complete validator inside verified OS network isolation after
installing the locked dependencies, as documented in `AGENTS.md`. It retains
subprocess isolation verification, lint/format/lock checks, all prior tests and
repeated simulator plus offline agent demos. This milestone remains uncommitted
for independent review; no hosted Phase 3B.4 run is claimed.

The [SQLite WAL-reset prerequisite](phase3b1.md#sqlite-prerequisite--live-use-blocked)
remains unresolved for the local runtime. Passing tests and a different hosted
SQLite cannot establish remediation. Live signer interoperability, real merchant
transport, RPC/ABI decoding, authoritative finalized-chain observation, wallet or
facilitator integration and Phase 3C remain blocked and separately authorized.
