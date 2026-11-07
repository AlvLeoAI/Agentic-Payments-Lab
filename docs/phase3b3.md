# Phase 3B.3 — offline evidence validation and explicit reconciliation

Implemented on published Phase 3B.2 commit
`3fc0cc1f67d9e8d014601ca312ccb35dabdee281` and published as
`00abf23ef55551e35bf2548bb731a670cd868904` after closure review.
This slice implements the bounded offline portion of step 6 of the
[Phase 3 plan](phase3-plan.md). It does not complete Phase 3B or authorize Phase 3C.
No credentials, real signing, wallet, OpenAI, RPC, facilitator, deployment or
retained-database migration is involved.

## Boundary and operator contract

`OfflineReconciler(store, observer, clock).reconcile(task_id)` is an explicit Python
operator operation. It reads the original enrolled task and persisted authorization;
it cannot initialize missing history, claim a purchase, mint a nonce or select new
terms. Its observer is `OfflineEvidenceObserver(FixtureSource(responses), limits)`.
`responses` is a mapping of fixture method names to synthetic JSON bytes, copied by
the source. There are no endpoint settings, HTTP/RPC clients, credentials or live
transport implementations. The fixture methods are not a JSON-RPC wire contract.

The four agent tools, deterministic service eligibility, CLI/default simulator,
merchant APIs and original gate remain separate. Reconciliation has no signer,
dispatcher, settlement method or delivery publisher. It never runs at startup,
after a merchant response, on a timer or as agent cleanup. An expired/revoked grant
does not prevent observing and accounting for an already authorized outcome.

The operator must supply synthetic records for the same existing fixture state.
Independent examples are in `tests/evidence_support.py`. A normal explicit flow is:

1. Retain the original buyer and merchant files and public nonce/terms after an
   interrupted offline exchange. Do not reconstruct a missing signature.
2. Construct the closed fixture source and `ObservationLimits` with a bounded
   block range. Invoke reconciliation once for the original task.
3. Inspect the authoritative buyer report. Incomplete evidence keeps exposure
   reserved; another observation requires another explicit operator invocation.
4. Retrieve an existing merchant delivery separately through its non-charging
   API. Reconciliation never creates/repairs a merchant observation or cache.

`apply_fixture` remains the original **trusted test-input primitive** for existing
tests. It does not independently obtain proof. The new operation validates detailed
records before using the same atomic accounting transition. Direct fixture input,
local objects and database administration remain trusted host capabilities; none
are exposed to the model or public merchant.

## Evidence and limits

The closed profile remains v2/exact/EIP-3009, `eip155:84532`, the pinned test USDC
contract and USDC/version-2 domain. Strict models reject coercions, extra fields,
unknown mechanisms, duplicate JSON keys, malformed JSON and oversized replies.
Addresses, hashes, atomic integers and timestamps have bounded types. Public call
arguments are normalized fixture data: no signature or raw transaction calldata
is accepted or persisted. This is **not** ABI decoding or signer interoperability.

| Conclusion | Required corresponding synthetic records |
| --- | --- |
| `fixture_settled` | Exact profile; stable finalized head; nonce used at that head; one matching `AuthorizationUsed` found in the bounded range; matching transaction target and complete public authorization arguments; successful receipt containing exactly one matching authorization event and transfer, with distinct log positions; canonical block tied to the same finalized head, within the authorization's validity bounds; all transaction/block/event identities agree |
| `fixture_unused` | Exact profile; stable finalized head strictly after `valid_before`; matching nonce state is unused at that exact block. The strict expiry boundary preserves Phase 3B.1 semantics |
| Incomplete | Read budget exhausted, source unavailable, timeout/cancellation, malformed/substituted/missing records, unstable head, incomplete discovery, ambiguous nonce use, canceled authorization, reverted receipt, unfinalized inclusion or unproven non-use |
| Operator incident | Coherent fixture counter-evidence contradicts previously accepted transfer/non-use or its finalized block. A later cancellation/nonce-used flag alone does not contradict earlier post-expiry non-use |

If a candidate and finalized head share either a block hash or height, their full
hash/height/timestamp identity must agree. A hash cannot identify an older block
with a different height or timestamp. Identical blocks and distinct ancestors
remain eligible. Head/canonical records are also checked against accepted evidence:
reusing its hash with another height latches an incident without changing accepted
accounting, including a head that would otherwise look stale.

Transfer observation makes at most **8** source calls: profile, finalized head,
nonce state, bounded authorization-event search, transaction, receipt, canonical
block and finalized-head recheck. Non-use requires 4. Limits permit a lower ceiling
from 1–8; attempts increment before awaiting a response, including failures.
There are zero automatic retries, paging, range expansion or polling. One configured
range contains at most 1,000 blocks; the search stops at the observed finalized head.
No matching event within that range is inconclusive, never non-execution proof.
Each reply is limited to 16 KiB, search results to 16 events and receipts to 32 logs.

The cooperative timeout defaults to 5 seconds and is configurable from 1–30.
It cannot stop blocking/uncooperative code or guarantee cleanup on process death.
Each SQLite write uses the existing 0.2-second lock wait; this is not a wall-clock
bound on filesystem I/O. Reconciliation does not retry a failed local commit.

These records establish consistency only within a trusted synthetic fixture.
They do not independently establish canonicality, finality, real nonce state,
token behavior or cryptographic authenticity. Every report retains
`mode=offline_x402`, `source=offline_fixture` and
`blockchain_settlement_verified=false`. HTTP success, merchant settlement
observations, cached content and transaction hashes alone cannot drive accounting.

## Ownership, persistence and reporting

| State | Owner / transition |
| --- | --- |
| Task, grant, purchase, nonce, execution ownership and reservation | Existing buyer database and Phase 3B.1 gate; identities remain immutable |
| Parsed observation | Existing buyer `events`, type `offline_reconciliation_observed`; only validated public fields, fixed reason codes and digests |
| Accepted terminal evidence and accounting | Existing `x402_attempts.evidence`, phase and grant balances; committed with the observation and `offline_fixture_applied` event in **one transaction** |
| Finality incident | Durable `offline_reconciliation_incident` event; new signing claims and dispatch permission across this one offline buyer profile fail closed, including an already claimed but undispatched purchase |
| Merchant settlement and delivery | Unchanged separate merchant file; no cross-file commit or merchant repair |

There is no additional database or table. **Buyer schema version 3** is a minimal
compatibility upgrade: the version-2 tables are unchanged, but older binaries must
reject the file instead of ignoring incident enforcement. The version marker
commits atomically after exact schema/profile validation. Missing, incompatible or
unsupported schemas still reject before persistent changes. Merchant schema stays
version 1. Existing Phase 2/version-1 migration fixtures continue to run, with only
six current-version assertion/failure-injection literals adjusted from 2 to 3.
A version-2 fixture generated with accepted Phase 3B.2 source verifies byte-identical
historical table rows, JSON, bindings, balances and evidence after upgrade. Fault,
abrupt-exit and concurrent-opener tests cover the version-2 upgrade as well.
No retained state is opened or migrated by validation.
Stop all old processes before an upgrade. The version check protects subsequent
openers; it cannot revoke an already-open older process. This is not a hot upgrade
or support for mixed-version writers. Concurrent openers running the new version
are covered. The accepted Phase 3B.2 binary was separately exercised against a
disposable version-3 file: it rejected at read-only preflight without changing
database bytes or creating a WAL.

Application writes append observations and incidents to the existing audit table.
Identical normalized records and their decision are deduplicated under
`BEGIN IMMEDIATE`; independent/concurrent processes cannot spend or release twice.
This is not a count of every operator invocation: read counts describe that
observation pass, not aggregate traffic across repeated calls. Incomplete or stale
observations cannot downgrade a terminal purchase. A finality incident preserves
accepted evidence, spent/reserved amounts and delivery; no automatic rollback,
unfreeze, replacement purchase or compensating payment exists.
An already delegated signer may finish; the incident prevents new dispatch
permission, not completion of that in-flight call or recall of an authorized dispatch.
Subsequent observations can be recorded while the profile is frozen, but cannot
change its accounting until a separately reviewed operator recovery exists.

The durable report reads balances, phase, ordered audit events, last observation
summary and incident status using one connection and explicit read transaction.
`reconciliation.last_observation` means the latest **distinct persisted observation**;
it need not describe the latest operator invocation. `observation_count` counts
these deduplicated audit records, not invocations.

`reconcile()` additionally returns a separate top-level `current_invocation` with
the observation hash, outcome, fixed reason, attempted reads and read limit for
that call. The decision comes from `record_observation` under its write transaction
and is returned only after commit, even when the audit event was deduplicated.
For success → unavailable → identical success, the current result reports the last
successful eight-read pass while the durable last observation remains the intervening
three-read failure; there are two audit observations and one accounting transition.
Another caller may commit between that decision and the report's read snapshot;
the two sections retain their separate meanings. A standalone `store.report()`
has no `current_invocation`. Cancellation, process exit or a lost acknowledgement
can prevent this transient result from reaching the caller; it is not a durable
per-invocation history or an aggregate read counter.

The report continues to leave buyer `delivery_state=not_requested`; this slice does not add buyer delivery
acceptance. Merchant delivery can already exist independently. The incident flag is
separate from the original purchase phase and `reconciliation_required` field.

## Interruption and recovery

| Boundary | Durable result / allowed action |
| --- | --- |
| Missing task, claim or incompatible state | Reject before fixture reads; never initialize or rebind |
| Read fails or budget ends | Append sanitized partial observation when persistence succeeds; preserve reservation |
| External cancellation before/after responses | One bounded best-effort partial-observation write, then fresh sanitized cancellation; no financial cleanup |
| Source swallows cancellation and returns late | Detect the task's cancellation request; do not accept the late result |
| Exit before record, after observation insert, after accounting writes, or before commit | Transaction absent/rolled back; original reservation and authorization remain |
| Commit succeeds but acknowledgement/report is lost | Accepted evidence, accounting and audit all exist; explicit identical observation is idempotent |
| Cancellation requested after accounting commit | Propagate cancellation without undoing accepted payment state or existing delivery |
| Contradictory final evidence | Persist incident and preserve accounting; stop affected execution for operator investigation |
| Upgrade interrupted before/after marker commit | Exact version 2 or 3 plus unchanged rows; explicit reopen can finish the upgrade/WAL configuration, without replaying execution |

Cleanup is not guaranteed. Abrupt termination, repeated cancellation, storage errors
or I/O stalls can prevent partial records or an incident latch from being persisted.
Persistence failure returns a fixed unavailable error and requires inspection;
never assume an incident was durably frozen merely because contradictory data was
seen in memory. Preserve surviving files and stop affected execution for review.
Repeated observation is a separate explicit action, never an execution retry.

Exception messages/notes, unparsed responses and signature/header markers are not
stored or returned. Tests inspect database/WAL bytes, reports, warnings and formatted
errors. Python tracebacks, debuggers and process memory may retain locals; sanitized
reporting and dropping references are not secure memory erasure.

Missing/partial history is not evidence of non-execution. Restoring an older complete
database can lose claims/evidence/incidents and remains outside the guarantee;
there is no backup framework, automatic repair or global external tombstone service.

## Validation and remaining work

Coverage includes lost settlement response followed by explicit reconciliation;
strict field substitutions/coercions; unavailable, malformed, provisional and
contradictory evidence; read ceilings; expiry boundaries; cancellation/timeout;
100 overlapping observations, separate-process reconciliation, and abrupt exits
before/within/after the accounting transaction. Authority-double records directly
establish synthetic settlement invocation and transfer counts. Signer/dispatcher
instrumentation checks that reconciliation introduces no new execution calls.
These counts are never described as blockchain charges.

Use the existing full validator after `uv sync --locked`:

```bash
parent_netns=$(readlink /proc/self/ns/net)
env -u OPENAI_API_KEY -u OPENAI_ORG_ID -u OPENAI_PROJECT_ID \
  PYTHONDONTWRITEBYTECODE=1 \
  unshare --user --map-root-user --net -- .venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

It must verify namespace isolation including subprocess inheritance, run every
test plus lint/format/lock checks and preserve repeated simulator/agent demos.
Failure to establish isolation is a validation failure, with no unrestricted fallback.
Local validation is separate from hosted CI.

Observed local validation after the focused review corrections: the complete
isolated validator exited 0 with **508 tests passed in 133.42 seconds** (493 before
correction, 15 additional regressions), Ruff passed, 65 Python files passed
formatting, and the offline lock check resolved 54 packages.
Both simulator demos were identical with one authority execution/charge of 10,000
atomic units. Repeated offline agent free/paid/unaffordable scenarios retained
authority execution/charge counts 0/0, 1/1 and 0/0. Namespace isolation and subprocess
inheritance were verified. Focused evidence/recovery/correction checks passed
(139 tests), as did all 37 unchanged external review probes. The new regression
file failed 11 cases against an isolated pre-fix source copy, with four controls
passing; all 15 pass with the corrections. No working-tree files were reverted
for that comparison. The subsequent publication evidence records **508 passing
tests** and verified namespace/subprocess isolation in the
[exact-SHA hosted run](https://github.com/AlvLeoAI/Agentic-Payments-Lab/actions/runs/42379074948).

Live use remains blocked by the documented unresolved local
[SQLite WAL-reset prerequisite](phase3b1.md#sqlite-prerequisite--live-use-blocked).
A green isolated test run or different hosted SQLite does not remediate it.
Real signer interoperability, RPC/ABI evidence decoding, finalized-chain observation,
facilitator integration, public merchant security, agent/backend integration and
coordinated restore recovery remain separately scoped work. Phase 3C still requires
explicit live authorization and independently verified prerequisites.
