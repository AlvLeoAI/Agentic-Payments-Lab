# Phase 3B.1 — offline validation and durable gate

Implemented on the accepted Phase 2 baseline
`9a2a49fd8eb4ac1fe55d9bac657f2e2f48301beb`. Starting branch: `main`; the only
preexisting work was the untracked [Phase 3 plan](phase3-plan.md) and
[data-access study](data-access-and-indexing.md). Those documents are preserved
unchanged as planning history, not treated as approval of their entire scope.

This is an **offline implementation milestone, not live x402 acceptance**. No
credential loading, wallets, cryptographic signing, RPC, facilitator requests,
OpenAI calls, deployment or repository publication is part of this pass. No
simulator or agent task can select this backend. The four agent tools, ASGI
merchant, simulator accounting and explicit reconciliation remain unchanged.

## Dependency decision

Pin **`x402==2.27.0` with no extras**. Compare the official
[2.26.0 distribution](https://pypi.org/project/x402/2.26.0/) (published October 28,
2026), [2.27.0 distribution](https://pypi.org/project/x402/2.27.0/) (November 4),
and [Python changelog](https://github.com/x402-foundation/x402/blob/main/python/x402/CHANGELOG.md).
The downloaded wheels were verified against their PyPI SHA-256 metadata and their
extracted source compared, rather than assuming the repository's current main
branch equals a release.

| Release source inspected | 2.26.0 → 2.27.0 |
| --- | --- |
| `schemas/payments.py`, `http/utils.py` | Byte-identical v2 models and header codecs |
| `mechanisms/evm/exact/client.py`, `mechanisms/evm/eip712.py` | Byte-identical exact/EIP-3009 creation/typed-data code |
| `facilitator.py`, `facilitator_base.py` | Advisory post-operation hook exceptions no longer discard underlying verify/settle results; before hooks remain gates |
| SIWX, EVM upto/Permit2, SVM | Nonce-storage checks, malformed-input handling and transaction/failure handling changes; none are activated here |

Wheel hashes:

```text
2.26.0 6cdcdfe553a9b698dca81357101fc6e98de35c5c6f4ea2b3cce344ea54486c1b
2.27.0 df76cac8ca44c6d73896f1759547f66acb06918f1f7da4a46836021f7f79f7f5
```

Both support Python >=3.10. Core requires Pydantic, typing-extensions and
nest-asyncio; the lock adds only x402 and `nest-asyncio==1.6.0`. Existing locked
packages and OpenAI pins (`openai==3.42.1`, `openai-agents==0.24.0`) stay unchanged.
2.27.0 retains the selected interfaces and includes upstream fixes, so there is
no reason to introduce the older release. This is a narrow compatibility finding,
not certification of every SDK mechanism.

Only the public v2 schemas and HTTP codecs are used. The SDK's broad models allow
defaults/coercion/extra data, so they do not replace application policy validation.
EVM extras would add eth-account/web3 and related packages without any need in
this milestone. The exact SDK client generates a nonce immediately before signing
and does not expose nonce/absolute-validity overrides. Instead, this slice owns
public signing terms and persists them before passing them to a double. A future
real signer bridge needs its own reviewed conformance tests. No private SDK
override, auto-payment HTTP wrapper, Permit2 fallback, sponsorship, automatic
settlement retry or nested-event-loop patch is used.

## Scope and authority

| Component | Responsibility and authority |
| --- | --- |
| `x402_contracts.py` | Frozen operator policy, public authorization terms, explicit offline evidence and future observer interface; vendor-independent |
| `x402_adapter/protocol.py` | Bounded HTTP 402 header parsing, exact policy comparison, SDK v2 decoding/encoding; no network client |
| `x402_store.py` | Buyer-file task binding, immutable authorization/nonce/owner, reservation, dispatch permission, cancellation fence, explicit fixture accounting and sanitized snapshot report |
| `x402_adapter/gate.py` | One awaited signer double and at most one dispatcher double; never retries a claimed attempt |
| `buyer_schema.py` | Local additive schema version and immutable backend profile; no cross-database upgrade assumption |
| Existing three simulator stores and agent metadata | Retain their original authority. They are not participants in an offline x402 attempt |

`OfflineGateStore.initialize(fresh_path, policy)` is an explicit operator-only
initialization contract. The path must not exist. `create_task` binds the request
and assigns an `offline-purchase-...` identity incorporating the state instance.
Reopen with `OfflineGateStore(path, exact_original_policy)`; reopening never creates
a missing file, grant, task or attempt. Missing task bindings and changed subjects
are rejected. The profile includes the complete immutable grant and policy.
`Ledger` accepts only simulation profiles, and the offline store refuses both
unversioned and migrated simulator databases. A task cannot switch backends.

No CLI option or model tool activates this module. Test doubles must declare
`mode="offline_double"`; signature values must be `OfflineSignature` markers
beginning `offline-test-signature:`. These are intentionally invalid EVM signatures.
This is an in-process interface contract, not a sandbox capable of proving an
arbitrary Python plugin harmless. Inject only reviewed doubles; OS network
isolation is the runtime enforcement during validation.

### Closed HTTP profile

Only an HTTP 402 `PAYMENT-REQUIRED` value with explicit `x402Version: 2`, exactly
one `accepts` entry and the approved resource URL is supported. Encoded header
limit: 8,192 ASCII bytes; decoded limit: 6,144 bytes. Malformed base64/JSON, duplicate
keys, coercions, extra fields, missing version, multiple choices and unknown
extensions are rejected before claim/signing. This deliberately rejects some
otherwise valid broader x402 messages (for example optional resource descriptions).

The selected requirements must match the operator's exact recipient, positive
integer amount encoded as canonical decimal text, and timeout (1–300 seconds), plus:

```text
scheme: exact
network: eip155:84532
asset: 0x036CbD53842c5426634e7929541eC2318f3dCF7e
extra: {name: USDC, version: "2", assetTransferMethod: eip3009}
```

The amount is denominated in six-decimal test-USDC atomic units **for fixtures**;
it is not a SIM-USDC balance or an on-chain balance. Network/token profile is fixed,
never inferred from a merchant response. Resource and GET/POST method must exactly
match the configured HTTPS endpoint, with no credentials/query/fragment. The bound
request's service and purpose must fit the grant. There is no arbitrary URL fetch.

The public EIP-3009 terms include payer, recipient, amount, nonce, `validAfter=0`,
and `validBefore=min(claim_time + approved_timeout, grant_expiry)`. EIP-712 domain
USDC/version 2/chain 84532/token and `TransferWithAuthorization` are also persisted.
Grant issuance/expiry and exact signing terms are checked before signing and again
before dispatch; revocation or late completion prevents dispatch. Request, policy
and requirements hashes bind local intent. These hashes are application SHA-256
fingerprints, not EIP-712 cryptographic hashes. EIP-3009 itself does **not** sign
the HTTP request, resource, task or purchase ID; future merchant enforcement is
still required. No cryptographic interoperability claim follows from fake headers.

### Gate transitions and failure matrix

Reservation and the single signing claim commit together with the public terms,
nonce, owner and audit event under `BEGIN IMMEDIATE` in the **same buyer file**.
Existing claims return status only, including expired/canceled/crashed claims.
Unique purchase/authorization/nonce constraints and immutable-row triggers reinforce
the API. No write transaction is held while awaiting either double.

`cancel(task_id, now)` is an operator operation restricted to an already enrolled
task under the store's bound grant. It commits an immutable task fence and audit
event even if no claim exists. Claim acquisition checks that fence under the same
write lock as reservation/ownership. A cancellation that commits first prevents
signing and dispatch, including an invocation already waiting to claim. Repeated
cancellation is idempotent and affects only the named task; it remains absent
from the four agent tools. A pre-claim cancellation reports `canceled_before_claim`,
no authorization, zero new reservation and no reconciliation requirement.

Normal return acknowledges the committed fence for a nonterminal task. A terminal
fixture remains terminal; cancellation does not undo it. Operator cancellation
persistence errors propagate and must not be reported as success. A lost commit
acknowledgment also cannot prove that the fence is absent: inspect the original
task. No cancellation resets a claim or creates a replacement authorization.

Dispatch permission is a second committed transition with a monotonic
`dispatch_authorized` marker, before invoking the dispatcher. Cancellation and
permission compete under the same write lock. The marker is separate from current
phase so an exception cannot erase whether dispatch may have occurred.

| Interruption / failure | Durable consequence / permitted next action |
| --- | --- |
| Rejected header, destination or policy | No claim, reservation, signer or dispatcher call |
| Cancellation commits before claim acquisition | Durable task fence, no authorization/reservation/signing/dispatch; restart returns the same cancellation |
| Cancellation fence/event/COMMIT fails and rolls back | Operator receives an error; no successful cancellation acknowledgment; inspect the original task |
| Claim INSERT/event/COMMIT fails and rolls back | No signing; reservation rolls back with claim |
| Claim commits but acknowledgment is lost | Attempt/nonce/reservation survive; no automatic signing on a repeated invocation |
| Signer in flight, fails, or signature lost | Reservation remains. Report requires reconciliation; never generate another nonce or re-sign |
| Cancellation wins before dispatch permission | `canceled_before_dispatch`; late signer result fails the compare-and-set; reservation remains |
| Signer suppresses external cancellation | Caller cancellation state is checked after await; late result cannot dispatch; cancellation propagates |
| Dispatch permission persistence fails | No dispatcher call; existing signing claim/reservation remain |
| Permission commits, process dies before dispatcher entry | Conservative “may have occurred”; no resend/release |
| Dispatcher starts, times out, fails, or response is lost | Same uncertainty and reservation, regardless of HTTP/facilitator success |
| Cancellation after dispatch permission | Dispatch may still complete. Cancellation cannot revoke an already granted permission; keep exposure |
| Explicit fixture evidence commit fails | Accounting, evidence, phase and event roll back together; no side effects are retried |
| Reopen after any ambiguous boundary | Validate schema/profile/history; return durable status only. No automatic reconciliation or replacement |

External cancellation performs one synchronous local fence attempt, with a 0.2s
SQLite lock wait, then propagates. Repeated cancellation, process death, disk errors
or I/O stalls can defeat cleanup; this is not a guaranteed wall-clock bound. If
cleanup fails, the previously committed claim still prevents a new attempt; a
missing cancellation event is not proof that cancellation did not happen. There
is no background worker or automatic lease expiration. External task cancellation
before the coroutine starts cannot run cleanup and does not acknowledge an
operator fence; use the explicit operator cancellation operation for that contract.

### Non-retention and evidence

The signature marker and encoded replay-shaped header are transient arguments only.
No database method accepts them. Reports/events allowlist non-secret fields and
fixed local reason codes; exception text and merchant/dispatcher response bodies
are not persisted or logged. Cancellation from a double or the caller propagates
as a fresh `CancelledError("offline_gate_canceled")`, without copying original
arguments/notes and with unsafe exception chaining suppressed. Cleanup failure
does not convert cancellation into success. Standard exception text and formatted
tracebacks are sanitized; this does not erase Python exception contexts, traceback
locals or memory accessible to arbitrary debuggers. Tests hold a reader open to
inspect actual WAL bytes
as well as the database, reports and captured logs/output. Releasing Python
references does not erase memory; debuggers, core dumps, swap, dependencies or an
untrusted double can retain copies. Disable such capture before any future real use.

After a crash the signed payload cannot be recovered from this journal. This
deliberately sacrifices availability: do not re-sign, resend or free a reservation
just because there is no signature. `reconciliation_required` remains true even
for a signing claim with no dispatcher entry. Retain the public terms/nonce and
seek authoritative evidence through explicit operator recovery.

`ReconciliationObserver` is an interface only. There is no live implementation or
startup caller. `apply_fixture` accepts only explicit `source="offline_fixture"`
evidence matching the entire terms hash, nonce, payer, recipient and amount. It
requires finalized transfer/success/nonce-use evidence or finalized non-use after
absolute expiry; pending, mismatched or contradictory evidence is rejected. The
fixture transaction updates reservation/spent amounts and state once. Conflicting
terminal fixtures cannot downgrade/release a prior outcome. Reports always set
`blockchain_settlement_verified: false`, use `fixture_settled` / `fixture_unused`,
and leave delivery `not_requested`. Fixture assertions are not independently
verified chain proof. HTTP success never calls this accounting operation.

## Persistence and upgrade

Only the buyer file changes. `PRAGMA user_version=0` with the **exact accepted
Phase 2 schema** upgrades to version 2: add immutable `buyer_profile`, `x402_tasks`,
`x402_attempts` and their constraints/triggers, plus `events(purchase_id)` and
`x402_cancellations`. Exact pre-correction Phase 3B.1 version-1 files retain their
existing profile, bindings, terms, nonce/owner identities, accounting and events;
the version-1 → 2 upgrade adds only the cancellation table and immutable-row
triggers. Existing claims remain single-attempt, including canceled claims.
Historical tables/columns/rows remain unchanged. New simulator files receive the
simulation profile. Existing simulator files cannot be enrolled into offline x402.
Unknown/newer/partially modified schemas and missing profiles fail closed; the
constructor does not repair them using `CREATE TABLE IF NOT EXISTS` guesses.

Existing files first undergo a read-only version/schema/profile check in one
snapshot, before any journal configuration. Validation repeats under
`BEGIN IMMEDIATE` before migration. Rejected unsupported files retain journal
mode, schema, version and data, including when an incompatible version appears
between preflight and lock acquisition. SQLite cannot switch to WAL inside that
transaction: WAL configuration follows a successful migration commit. If journal
configuration then fails, opening raises; the complete upgraded schema survives
and explicit reopening can finish configuration without replaying financial work.

DDL and version update share one transaction. DDL statements are executed
individually, because `executescript` would implicitly commit. Offline profile
initialization and initial grant insertion are separate local commits; interruption
between them leaves an unusable fixture requiring explicit inspection/recreation,
not grant reconstruction. There is no multi-file atomicity claim or general
migration/backup framework. Existing simulator authority, provider and agent files
are untouched by this migration and retain their original schemas.

For any retained state, stop writers, take a consistent SQLite backup including
all related stores, verify the exact old schema, and test a disposable copy before
opening it with the new code. Do not copy an actively changing DB without its WAL.
**No retained live-smoke database was opened or migrated in this pass.** All
development and failure injection used fresh temporary directories. The checked-in
SQL fixture was generated from an isolated copy of the accepted Phase 2 source,
with synthetic requests, a settled/delivered purchase, denied purchase, reserved
purchase, original agent witness, quotes and events. Upgrade tests compare every
original row byte-for-byte, including serialized receipts and bindings.
`tests/fixtures/phase3b1_v1_ledger.sql` was generated from the isolated pre-correction
source, with synthetic enrolled tasks, outstanding claims, cancellation and
dispatch uncertainty. Upgrade tests preserve all original rows and verify new
cancellation fences survive reopen. Migration cannot recover a cancellation that
the version-1 defect never recorded. Operators must review old unclaimed tasks
and explicitly cancel them where appropriate before allowing execution; no
original intent is inferred from missing history or invented by the migration.

Normal recovery: stop new execution, preserve DB/WAL evidence, inspect the original
profile/task/authorization and audit history, and obtain a conclusive observation
for that **same** nonce. Phase 3B.1 can exercise only explicit fixture evidence;
real recovery remains blocked. Missing records cannot be recreated from new caller
arguments. A full rollback/restore of an internally consistent older database can
erase all local witnesses; local identities cannot detect that. Filesystem admins
and application plugins are trusted, and SQL triggers are not tamper-proof.

Filtered `Ledger.audit` now uses an equality query; unfiltered audit remains an
ordered full scan. `export_audit` retains its original allowlist and shared read
transaction. The new report also reads state/balance/events in one snapshot.
Query-plan tests check indexed search without a temporary sort, index column
structure, filtered/global/missing histories and order; they avoid exact EXPLAIN
text matching. No speculative tool-event/JSON/search indexes were added.

## SQLite prerequisite — live use blocked

Observed locally on November 6, 2026:

```text
Python: 3.12.3, GCC 13.3.0; Ubuntu python3.12 3.12.3-1ubuntu0.17
Extension: /usr/lib/python3.12/lib-dynload/_sqlite3.cpython-312-x86_64-linux-gnu.so
Loaded library: /lib/x86_64-linux-gnu/libsqlite3.so.0
Resolved library: /usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6
sqlite_version(): 3.45.1
sqlite_source_id():
2024-01-30 16:01:20 e876e51a0ed5c5b3126f52e532044363a014bc594cfefa87ffb5b82257ccalt1
Vendor package: libsqlite3-0 3.45.1-1ubuntu2.8
Library SHA-256:
85265a9d4afca6f4b325ceb078b669c754fb881abed4cafe91ccebe9d625d975
```

`ldd` confirms this dependency; `/proc`-independent interpreter queries report its
version/source/build options (58 compile options captured in external evidence).
`dpkg -V libsqlite3-0` reports no changed package files. The vendor changelog and
the matching [Ubuntu source patch archive](https://archive.ubuntu.com/ubuntu/pool/main/s/sqlite3/sqlite3_3.45.1-1ubuntu2.8.debian.tar.xz)
were inspected, including every patch in `debian/patches/series`. No patch changes
`src/wal.c` or implements the WAL-reset fix; unrelated FTS/session/security fixes
do not establish WAL safety. Archive SHA-256:
`d543bf91c6f14ccd52ebe44d6ef3b360196b2596e0a7ca86089aef5fc52f5ec0`.

[SQLite documents the WAL-reset race](https://sqlite.org/wal.html#walresetbug)
as affecting older WAL releases and fixed in 3.51.3+, with 3.44.6/3.50.7 backports.
Concurrent write/checkpoint connections are relevant to this project. The installed
vendor source provides no evidence of that backport; treat the runtime as unpatched
for acceptance. Passing tests cannot disprove a rare corruption race. A reproducible
build/verified vendor fix is required before live use. No system package changes
or unsafe attempt to reproduce corruption on retained state were made.

## Validation and remaining work

Dependencies are installed with `uv sync --locked` before entering isolation. Run
the existing fail-closed validator using a fresh network namespace:

```bash
parent_netns=$(readlink /proc/self/ns/net)
unshare --user --map-root-user --net -- \
  .venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

The validator verifies a different namespace, loopback-only interfaces and child
inheritance, then runs offline lock validation, Ruff, formatting, the entire suite,
two simulator demos and repeated free/paid/unaffordable scripted agent scenarios.
The existing authority-database charge assertions remain intact. New tests count
signer/dispatcher entries at independent doubles; subprocess crash tests fsync
separate call markers before `os._exit`. No hardcoded `signing_attempts` statistic
is used as instrumentation.

Initial implementation results (November 6, 2026, before these corrections):
the complete validator exited **0**;
**256 tests passed in 54.76s** (151 existing tests preserved, 105 added). Ruff
reported all checks passed, formatting reported 46 files already formatted, and
`uv lock --check --offline` resolved the locked 54 packages successfully. The
namespace check confirmed loopback-only isolation inherited by subprocesses.

| Independently measured scenario | Observed result |
| --- | --- |
| 100 concurrent calls for one offline x402 purchase | 1 signer-double entry, 1 dispatcher-double entry, 1 durable attempt/identity; 10,000 reserved, 0 spent |
| 20 offline x402 tasks with budget for 3 | 3 signer entries, 3 dispatcher entries, 17 budget rejections; 30,000 reserved, 0 spent |
| Cancellation before dispatch, including a signer swallowing cancellation | 1 signer entry, 0 dispatch entries; 10,000 remains reserved |
| Abrupt exit with signature lost / after dispatch permission | 1 signer entry, 0 dispatch entries; reopen adds 0/0 calls |
| Abrupt exit after dispatch entry / after response | 1 signer entry, 1 dispatch entry; reopen adds 0/0 calls |
| Repeated original simulator demo | 2 identical results; authority DB: 1 execution, 1 simulated charge, 10,000 atomic units |
| Repeated free / paid / unaffordable scripted agent scenarios | 2 runs each; authority DB execution/charge counts 0/0, 1/1, 0/0 respectively |

These are offline observations. The gate's fixture accounting is not a payment
authority or blockchain charge. Full validator output, dependency comparison,
SQLite build/patch evidence and an unstaged review patch are retained outside the
repository under `/tmp/payments-phase3b1-1qszsfx9/` (ephemeral local artifacts).
No hosted GitHub Actions run was triggered or observed for these changes.

Initial implementation files changed in this slice: `AGENTS.md`, `README.md`,
`pyproject.toml`, `uv.lock`
and `src/agentic_payments_lab/ledger.py`. Added: `buyer_schema.py`,
`x402_contracts.py`, `x402_store.py`, and `x402_adapter/{__init__,protocol,gate}.py`
under that package; `tests/test_x402_gate.py`, `tests/test_x402_recovery.py`,
`tests/x402_crash_worker.py`, `tests/fixtures/phase2_ledger.sql`, and this document.
The two prior planning documents, license, agent implementation, existing tests,
CI workflow and validator script were preserved unchanged. Nothing was staged,
committed or pushed.

### Independent-review corrections

The review at `/tmp/payments-phase3b1-review-f6dz3xh1/REVIEW.md` identified lost
pre-claim cancellation, cancellation exception disclosure and WAL mutation on
unsupported-schema rejection. All four failing review cases are retained in
`tests/test_x402_corrections.py`. The identical four-case regression file failed
against the saved pre-correction Phase 3B.1 source for the original assertions
(not imports/setup), then passed against the corrections. The accepted Phase 2
commit was not used as a substitute baseline.

Additional coverage includes cancellation persistence errors/lost acknowledgments,
lock ordering, process exit/restart, signer/dispatcher/external cancellation and
cleanup errors, version-1 migration preservation/concurrency/failure, and the
separate WAL-configuration failure boundary. The old snapshot and review evidence
remain outside the repository. This corrective pass changes the schema opener,
storage setup option, gate/store, focused tests/fixture/worker and documentation;
dependencies, agent tools, payment semantics and the SQLite runtime are unchanged.
Passing these checks does not resolve the WAL-reset prerequisite or establish
real signer, merchant or chain-observer interoperability.

Corrective validation observed locally on November 6, 2026: all four original
regressions failed for their expected behavioral assertions against the saved
source (**4 failed in 0.57s**), then the same file passed against the correction
(**4 passed in 0.46s**). The complete validator ran once under verified OS network
isolation inherited by subprocesses: **288 passed in 66.62s** (256 prior cases,
32 added), Ruff passed, 47 files passed formatting, and the offline lock check
resolved 54 packages. Both simulator demos were identical with one execution/charge
and 10,000 atomic units in the payment authority. Repeated free/paid/unaffordable
agent scenarios retained execution/charge counts 0/0, 1/1 and 0/0 respectively.

The independent review probes also passed: **71 passed in 5.90s**. Their four
original failing expectations were preserved. Only two final schema-version
literals changed from 1 to 2 in a separate probe copy, as required by this migration;
the original review files remain untouched. Evidence, exact commands, the 54-file
pre-correction source snapshot and a corrective-only diff are retained in
`/tmp/payments-phase3b1-corrections-58bgbw2d/` (ephemeral local artifacts).
No hosted workflow was run, no retained runtime database was migrated, and no
credentials, live operations, system-package changes or repository publication
were involved.

Before full Phase 3B completion: review this diff and offline state machine; add
the separately approved merchant-side request/payment-id/nonce binding, durable
settlement and delivery deduplication, an explicitly controlled HTTP integration,
and real-signer interoperability tests with reviewed custody interfaces. Preserve
the no-retry rule and determine how lost signatures can be conclusively reconciled.
Do not infer these integrations are authorized by this slice.

Before Phase 3C: resolve the SQLite WAL fix, approve wallet/custody and funding
separately, verify current facilitator/network/token support, implement and review
chain observation/finality/non-use proof, bounded time/network behavior and safe
operational recovery. Live credentials, signing and payments require separate
explicit authorization. This pass neither runs hosted CI nor publishes a commit.
