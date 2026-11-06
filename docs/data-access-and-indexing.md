# Data access, indexing and Phase 3 persistence

Status: inspected and measured offline on 2026-11-03; changes below are proposals.
Baseline: `9a2a49fd8eb4ac1fe55d9bac657f2e2f48301beb`, initially clean `main`.
Companion: [Phase 3 architecture and acceptance plan](phase3-plan.md).

## Ownership and transaction boundaries

The accepted implementation has four SQLite files, not three when the buyer agent
is enabled. `storage.Database` opens a new connection for each operation, enables
foreign keys, configures WAL and `synchronous=FULL`, and uses `BEGIN IMMEDIATE` for
writes. Financial connections wait up to 30 seconds; agent metadata uses 100 ms.
There is no `ATTACH`, distributed transaction or automatic startup reconciliation.

| Store | Authority | Loss / interruption consequence |
| --- | --- | --- |
| `ledger.sqlite3` | Grants, immutable task/purchase identity, accounting, approved intents, observed purchase state, original agent binding witness, audit events | Missing financial history is not proof of non-execution; preserve evidence and require operator recovery |
| `payments.sqlite3` | **Simulation-only** authorization outcomes and execution attempts | Buyer `executing`/`unknown` can lag a committed simulated charge; explicit lookup reconciles the same authorization |
| `provider.sqlite3` | Merchant quotes and immutable cached delivery | Settlement can succeed without a buyer delivery acknowledgment; cache retrieval is a separate operation |
| `agent.sqlite3` | Bindings, quote handles, tool/run/usage evidence | No money authority; missing binding fails closed against the ledger witness; lost usage remains unknown |

The ledger's single-store transaction owns both reservation/spend updates and the
purchase transition/event. A transaction in another file cannot share that atomicity.
An audit export uses one explicit read transaction for its purchase and events,
not a snapshot of all stores. Keep that guarantee when adding indexes or evidence.
The current stores assume retained consistent local disk state and a trusted host;
WAL is not a multi-file backup protocol. SQLite explicitly documents that attached
WAL databases are individually atomic rather than atomic as a group:
[WAL overview](https://www.sqlite.org/wal.html), retrieved 2026-11-03.

## Existing schema and index inventory

### Runtime prerequisite

The inspected Python runtime is 3.12.3, linked to SQLite 3.45.1; an in-memory
`SELECT sqlite_source_id()` returned
`2024-01-30 16:01:20 e876e51a0ed5c5b3126f52e532044363a014bc594cfefa87ffb5b82257ccalt1`.
Upstream's [WAL-reset bug advisory](https://www.sqlite.org/wal.html#walresetbug)
describes a rare multi-connection write/checkpoint corruption race and fixes in
3.51.3+, 3.44.6 and 3.50.7. Our reported version is within the upstream affected
range; whether this vendor build contains a backport is unknown. No corruption
was observed or sought in existing runtime data. Before testnet use, verify a fixed
library through the actual Python interpreter and the selected CI/runtime image;
Python version and `uv.lock` alone do not pin system SQLite. No upgrade or workaround
was attempted here. SQL query-plan analysis does not test this race.

### Tables and constraints

Inspected the `SCHEMA` constants in `ledger.py`, `payments.py`, `provider.py` and
`buyer_agent/store.py`, and used `PRAGMA table_info`, `index_list` and
`foreign_key_list` on disposable copies of those exact schemas. **There are no
explicit application `CREATE INDEX` statements.** The indexes below are SQLite's
automatic PK/UNIQUE indexes; integer sequence keys are rowid aliases rather than
separate indexes. `sqlite_sequence` is an internal AUTOINCREMENT table.

| Store.table | Primary key | Other unique key / index | Other correctness constraints |
| --- | --- | --- | --- |
| ledger.grants | `grant_id` TEXT | PK autoindex | Integer nonnegative budget/reserved/spent; `reserved+spent<=budget`; revoked 0/1 |
| ledger.quotes | `quote_id` TEXT | PK autoindex | Non-null serialized data; application rejects changed content for existing ID |
| ledger.tasks | `task_id` TEXT | UNIQUE `purchase_id` | Non-null grant/purchase; host derives purchase identity |
| ledger.agent_bindings | `task_id` TEXT | PK autoindex | Only declared application FK: task_id → tasks; update/delete blocked by triggers |
| ledger.purchases | `purchase_id` TEXT | UNIQUE nullable `authorization_id` | Nonnegative integer amount; enumerated payment/delivery states; non-null request/quote/fingerprint/grant/policy |
| ledger.events | `sequence` INTEGER AUTOINCREMENT | None | Append-only update/delete triggers; purchase_id nullable for grant events |
| payments.authorizations | `authorization_id` TEXT | UNIQUE `purchase_id` | Non-null intent/result; immutable identity compared by application |
| payments.attempts | `sequence` INTEGER AUTOINCREMENT | None | Non-null authorization_id/time; no per-authorization index |
| provider.quotes | `quote_id` TEXT | PK autoindex | Non-null data |
| provider.deliveries | `purchase_id` TEXT | PK autoindex | Non-null fingerprint/data; application compares fingerprint before reuse |
| agent.bindings | `task_id` TEXT | PK autoindex | Non-null data; compared against immutable ledger witness |
| agent.quote_handles | `handle` TEXT | PK autoindex | Non-null task/request/quote; lookup checks task as well |
| agent.tool_events | `sequence` INTEGER AUTOINCREMENT | None | Non-null run/data |
| agent.runs | `run_id` TEXT | PK autoindex | Non-null report JSON; progress updates must affect exactly one row |
| agent.recovered_bindings | `task_id` TEXT | PK autoindex | Recovery-history marker |

Do not confuse `PRAGMA foreign_keys=ON` with declared relationships: purchases →
grants, tasks → grants, events → purchases, and agent tool events → runs are not
SQL foreign keys in this baseline. Cross-file foreign keys do not exist. Current
typed application checks carry much of the identity contract. TEXT primary keys
in these ordinary SQLite rowid tables also lack explicit `NOT NULL`; SQLite permits
NULL in such keys. Strict domain validation prevents that through current entry
points. Do not silently bundle wholesale schema hardening into an index change;
new Phase 3 correctness keys should be explicitly non-null.

## Query inventory

Grouped below by distinct access/write shape, including repeated statements used by
several methods. Source line references are relative to the accepted SHA. SQL uses
bound parameters; table/column names are static. This inventory covers application
SQL plus the direct diagnostic/test access, not hypothetical future queries.

### Buyer ledger — `src/agentic_payments_lab/ledger.py`

| Methods / lines | SQL access shape | Existing path / purpose |
| --- | --- | --- |
| `_event`, 96 | INSERT event with purchase_id, type, JSON, time | Append rowid; no secondary index maintenance |
| `add_grant`, 103–113 | SELECT data WHERE grant_id; INSERT grant/data/budget | PK lookup then immutable-content comparison |
| `revoke_grant`, 117 | UPDATE revoked WHERE grant_id | PK; event in same transaction |
| `create_task`, `task`, 131–142 | SELECT task WHERE task_id; INSERT task/grant/purchase | PK read and unique purchase identity |
| `approved`, 151 | SELECT intent,state WHERE purchase_id | Purchase PK; exact durable intent/dispatch comparison |
| `agent_binding`, `initialize_agent`, 164–180 | SELECT binding/task by task_id; SELECT purchase existence by purchase_id; INSERT witness | Indexed single-row access; witness committed before separate metadata copy |
| `record_quote`, `quote`, 185–195 | SELECT data WHERE quote_id; INSERT OR IGNORE quote | PK; canonical-content check before ignoring conflict |
| `_validate`, 201–209 | SELECT grant WHERE grant_id and quote data WHERE quote_id | Both PKs; policy validation in caller's transaction |
| `reserve`, 226–244 | SELECT task grant WHERE purchase_id; SELECT existing fingerprint WHERE purchase_id; UPDATE grant reserved+=amount WHERE grant_id AND budget-spent-reserved>=amount | Task unique index, purchase PK, grant PK. Conditional update is the concurrency guard; budget expression needs no index |
| `reserve`, 268 | INSERT purchase snapshot/intent/state | PK and unique authorization protect identity; events in same transaction |
| `claim`, 302–327 | SELECT purchase by PK, revalidate grant/quote; UPDATE purchase executing or denied; decrement reserved on denial | Indexed one-purchase transaction; durable claim before adapter |
| `cancel_reserved`, 336–346 | SELECT purchase by PK; if reserved UPDATE failed and decrement grant reserved | Same lock as claim; no scan or remote operation |
| `resolve`, 354–371 | SELECT purchase by PK; conditional application-state check; UPDATE grant reserved/spent and purchase state/error/receipt | Accounting and purchase update atomic; stale terminal callbacks return without spending again |
| `record_delivery`, 387 | UPDATE purchase WHERE purchase_id AND payment_state IN (...) AND delivery_state!='delivered' | PK candidate; predicates prevent stale overwrite |
| `record`, `outcome`, 410 | SELECT purchase WHERE purchase_id | PK; outcome parses stored JSON |
| `budget`, `buyer_constraints`, 435–448 | SELECT grant WHERE grant_id | PK; consistent grant/balance view from one row |
| `audit`, 466 | SELECT events WHERE (? IS NULL OR purchase_id=?) ORDER BY sequence | Full scan even with bound non-null purchase in measured plans |
| `reconciliation_started`, 472 | INSERT event through `_event` | No search by payment state or automatic work queue |
| `export_audit`, 477–489 | BEGIN; SELECT purchase by PK; SELECT events WHERE purchase_id=? ORDER BY sequence; end transaction | Purchase indexed; event scan currently. Both reads share one WAL snapshot |

### Payment authority — `src/agentic_payments_lab/payments.py`

| Methods / lines | SQL access shape | Existing path / purpose |
| --- | --- | --- |
| `_stored`, 64 | SELECT authorization WHERE authorization_id=? OR purchase_id=? | SQLite multi-index OR uses both uniqueness indexes; application then compares complete intent |
| `execute`, 78–105 | INSERT attempt; `_stored`; INSERT authorization/intent/result | Write transaction commits authority result and attempt; duplicate calls may add attempts but not a second logical charge |
| `lookup` | `_stored` only | Missing row yields unknown, not failed |
| `cancel`, 131 | INSERT authorization ON CONFLICT(authorization_id) UPDATE result | Same authority write lock fences delayed simulation execution |
| `statistics`, 145–148 | SELECT all results; SELECT count(*) FROM attempts | Intentional full-table aggregate; Python parses result JSON; no filtered amount query |

`signing_attempts=0` is a constant in simulator statistics, not instrumentation.
Future signer counts need actual recorded calls, and charge assertions need the
relevant payment authority, not just purchase outcomes.

### Merchant — `src/agentic_payments_lab/provider.py`

| Methods / lines | SQL access shape | Existing path |
| --- | --- | --- |
| `quote`, 86 | INSERT OR IGNORE quote/data | Quote PK |
| `deliver`, 94 | SELECT quote WHERE quote_id | Quote PK; receipt verified against separate simulator authority |
| `deliver`, 122–145 | SELECT delivery WHERE purchase_id; compare fingerprint; INSERT delivery | Delivery PK in transaction; cached result committed before response |

### Agent metadata — `src/agentic_payments_lab/buyer_agent/store.py`

| Methods / lines | SQL access shape | Existing path |
| --- | --- | --- |
| `initialize`, 47–53 | SELECT binding existence WHERE task_id; INSERT binding | PK; independent ledger witness committed first |
| `recover`, 61–67 | SELECT binding by task_id; INSERT binding; INSERT OR IGNORE recovery marker | PKs; never reconstruct goal from new arguments |
| `bind`, 78–84 | SELECT binding and recovery marker WHERE task_id | Two PK lookups; compared with ledger witness |
| `quote`, 94 | INSERT OR IGNORE handle/task/request/quote | Handle PK; task-bound digest |
| `resolve`, 107 | SELECT handle WHERE handle=? AND task_id=? | Handle PK selects at most one; task predicate is a correctness check, not a reason for another index |
| `event`, 118 | INSERT run_id/data into tool_events | Append-only by API; no current application SELECT by run_id |
| `start`, 125 | INSERT run_id/report | Run PK; committed before model request |
| `progress`, `finish`, 132 | UPDATE run data WHERE run_id | PK; require affected row count 1, never UPSERT missing run |

### Diagnostics, tests and storage setup

`scripts/validate_offline.py` directly reads all payment authority results and counts
attempts after repeated simulator/agent demos. `test_buyer_agent.py` and
`test_agent_durability.py` use the same shapes; they also read all run reports
(sometimes `ORDER BY rowid`) and count tasks/purchases. Durability tests intentionally
DELETE a binding or INSERT legacy binding metadata. `test_authorization.py` attempts
DELETE of audit events to verify rejection. These are test probes, not production
maintenance/cleanup queries. There is no current application query joining runs to
tools, searching JSON by subject, scanning pending payments, or doing full-text search.

Storage setup executes WAL/FULL/foreign-key PRAGMAs and schema DDL; transaction
control executes BEGIN IMMEDIATE, COMMIT/ROLLBACK, or the explicit audit BEGIN.
The conditional audit regression intercepts SELECTs to arrange concurrent
reconciliation. An index must not remove that snapshot requirement.

## Disposable query-plan experiment

Executed with the existing environment's `.venv/bin/python`, SQLite **3.45.1**.
Only AST extraction of literal `SCHEMA` values was used; application constructors
and existing state directories were not opened. Four temporary databases were
created under `/tmp/payments-phase3a-iy37g3i2/`.

Synthetic dataset:

- Ledger: 10,000 purchase rows and 100,000 interleaved events, ten per purchase.
- Payment authority: 10,000 authorization/purchase pairs.
- Agent: 100,000 interleaved tool events for 10,000 run IDs.
- Minimal placeholder JSON satisfies SQL columns, not full domain schemas. These
  databases are query-plan fixtures, not valid application backups or financial tests.
- `ANALYZE` before each comparison; select purchase/run `42`. No timing, throughput,
  production-size claim, WAL crash experiment or concurrent load benchmark.

Exact commands executed (external artifacts, not committed):

```text
.venv/bin/python /tmp/payments-phase3a-iy37g3i2/sql_probe.py
.venv/bin/python /tmp/payments-phase3a-iy37g3i2/sql_index_alternative.py
```

`sql-plans.json` records schema/index inventory and before/after plans. The second
probe tested a narrower index after the first experiment. Both scripts exited 0.

| Query | Baseline plan | With candidate index |
| --- | --- | --- |
| Export events WHERE purchase_id=? ORDER BY sequence | `SCAN events` | `(purchase_id,sequence)`: `SEARCH events USING INDEX events_purchase_sequence (purchase_id=?)`; no temporary sort |
| Current audit WHERE (? IS NULL OR purchase_id=?) ORDER BY sequence, non-null ID | `SCAN events` | Still `SCAN events` |
| Same audit with NULL (all history) | `SCAN events` | Still scan, which is appropriate for requesting all rows |
| Purchase WHERE purchase_id=? | `SEARCH purchases USING INDEX sqlite_autoindex_purchases_1` | Unchanged |
| Authorization WHERE authorization_id=? OR purchase_id=? | `MULTI-INDEX OR` using `sqlite_autoindex_authorizations_1` and `_2` | No added index needed |
| SELECT result FROM authorizations | `SCAN authorizations` | No added index proposed for a full export/aggregate |
| Proposed tool history WHERE run_id=? ORDER BY sequence | `SCAN tool_events` | `(run_id,sequence)`: `SEARCH tool_events USING INDEX tool_events_run_sequence (run_id=?)` |

The narrower alternative `CREATE INDEX events_purchase_only ON events(purchase_id)`
also produced an indexed purchase search without a temporary sort. SQLite secondary
indexes include the rowid, and `sequence INTEGER PRIMARY KEY` aliases that rowid.
It also supported `purchase_id=? AND sequence>? ORDER BY sequence LIMIT ?` with
`(purchase_id=? AND rowid>?)`. See [query planning](https://www.sqlite.org/queryplanner.html)
and [EXPLAIN QUERY PLAN](https://www.sqlite.org/eqp.html), retrieved 2026-11-03.

Reproducible core experiment, using any disposable copy of the existing event schema:

```sql
-- Populate 100,000 interleaved events for 10,000 IDs, then ANALYZE.
EXPLAIN QUERY PLAN
SELECT * FROM events WHERE purchase_id='p42' ORDER BY sequence;

CREATE INDEX events_purchase_id ON events(purchase_id);
ANALYZE;
EXPLAIN QUERY PLAN
SELECT * FROM events WHERE purchase_id='p42' ORDER BY sequence;

-- Bind both placeholders to 'p42'; the optional-filter shape still scans.
EXPLAIN QUERY PLAN
SELECT * FROM events WHERE (? IS NULL OR purchase_id=?) ORDER BY sequence;
```

### Index decisions

1. **Propose `events(purchase_id)`**, with the existing INTEGER sequence PK retained.
   It is sufficient for the measured equality/order/range patterns; the explicit
   composite is a valid alternative if rowid layout changes. Do not add both.
2. **Propose splitting `Ledger.audit` SQL**: no purchase argument uses the ordered
   full scan; a purchase argument uses `WHERE purchase_id=? ORDER BY sequence`.
   Preserve exact event ordering and return shape. Test NULL/global grant events,
   a missing purchase history, and many interleaved purchases. `export_audit` keeps
   its single read transaction and sanitization unchanged.
3. **Defer tool-event index** until adding an actual per-run history retrieval
   operation. The proposed query benefits, but no current application read uses it.
   `tool_events(run_id)` would likewise carry the integer sequence rowid; measure
   the chosen real query before adding a composite.
4. **No new indexes** for handle+task, budget arithmetic, quote ID, purchase ID or
   the authority OR query. Existing PK/UNIQUE indexes already select a bounded row
   set. Indexes cannot enforce task binding, available budget or immutable content.
5. **Defer pending-state, JSON-expression, time/expiry, grant-history and attempt-history
   indexes** until those reads exist. If an operator pending list is implemented,
   normalize the filter columns and test a partial index on unresolved rows. Do not
   introduce a background scheduler merely to justify it.

An added event index increases write work and disk/WAL volume. The experiment proves
planner access choices only. Measure representative append/export workloads later if
latency matters. Keep exports bounded operationally; keyset pagination is a possible
future API, but separate read transactions across pages do not preserve one snapshot
automatically. No pagination or export-schema change is proposed in this pass.

## Proposed Phase 3 persistence

No schema change was made. The following is a minimal implementation contract for
3B, not executable migration SQL or a general backup framework.

| Location | Proposed record / constraints | Reason |
| --- | --- | --- |
| Ledger | Explicit state metadata: schema version, instance ID, backend, network/asset, immutable policy/config digest; enrollment required | Never reinterpret simulator balances as test USDC; reject missing or mismatched state on normal startup |
| Ledger | One x402 intent row per purchase: NOT NULL PK/FK purchase_id, immutable approved terms/request fingerprint, signer address, payment identifier, absolute deadline | Approval and reservation commit in the same database; no agent-store authority |
| Ledger | Signing journal associated with that intent: one signing claim, typed-data digest, nonce, validity bounds, payload digest, dispatch marker; UNIQUE `(network, asset, payer, nonce)` with non-null values once assigned | Prevent fresh authorization on restart; persist full non-secret authorization tuple before delegation. State constraints distinguish an unassigned nonce from a damaged claimed row |
| Ledger | Append-only observations with source/type/time, nonce, tx hash, block number/hash, log positions, finality level, evidence digest and bounded validated detail | Distinguish provisional observations from accepted terminal evidence; deduplicate settlement/accounting by purchase, not response arrival order |
| Merchant | One operation per merchant-scoped payment identifier; immutable body/method/path/terms fingerprint; unique nonce tuple; settle claim, terminal observation, delivery/cache state | Race-safe operation identity before settle. A new ID cannot reuse an authorization for another resource |
| Merchant | Result content with explicit retention deadline, plus permanent identity/evidence tombstone | Expired content cannot reopen payment or business execution |
| Agent metadata | Existing task witness/handles/run records; additive backend-aware public summary only where required | No wallet credentials, signatures or raw payment headers; financial evidence remains in ledger |

Keep signed payloads ephemeral in the initial design. Persisting only the nonce,
typed-data parameters and digests allows chain reconciliation without retaining a
transfer-capable signature. It also means a restart cannot resume sending that
payload. This is an explicit liveness tradeoff in the [failure matrix](phase3-plan.md#failure-matrix).
Do not silently add an encrypted payload store/key management system to solve it.

The simulator payment authority stays unchanged. No new SQLite file is needed for
x402 execution evidence. Network and token become part of every authorization
identity; application payment IDs do not acquire authority from being hashes.
Keep existing ledger budget accounting in integer units and restrict each initialized
state to one backend/network/asset so a grant cannot mix incomparable balances.

### Migration and recovery contract

- Introduce explicit per-file schema versions before adding Phase 3 tables. Review
  exact baseline schemas; do not infer successful migration from `CREATE TABLE IF
  NOT EXISTS`. Unknown/newer versions or inconsistent mode/config fail closed.
- Preserve original simulator schemas, values and JSON contracts. Prefer additive
  tables and separate opt-in testnet state over rewriting accepted purchases. Review
  any domain/evidence changes against archived Phase 1/2 fixture data.
- Execute each file's migration in its own transaction with version update in that
  transaction. An index creation is local to that database. No multi-file atomic
  upgrade is claimed; refuse runtime until all required version/instance checks pass.
- Require operator maintenance with processes stopped and a recoverable consistent
  snapshot before migrating existing state. Copying a live `.sqlite3` file without
  WAL/SQLite backup handling is not a valid backup. New testnet state should be
  initialized explicitly and should not migrate or open existing `.lab` directories
  by default.
- Bind merchant and buyer state instance identifiers during explicit setup. Normal
  startup may validate versions but never initialize missing finance, adopt a new
  signer, reconcile, renew authorization or create a replacement purchase.
- A partial restore can still reproduce internally valid but stale records. Local
  IDs do not solve rollback detection if all copies are lost/restored together.
  Stop new signing; compare retained records with chain/merchant evidence through
  an operator recovery process. No automatic repair from caller arguments.
- Migration tests on disposable fixture copies must prove row identities, spent and
  reserved balances, original agent witnesses, events and simulator receipt payloads
  unchanged. Interrupt between per-file commits; restart must refuse an incomplete
  upgrade. Leave destructive migration/backup automation out of 3B.

## Discovery, full-text and semantic indexing

The current catalog has three operator-owned services, and `Boundary.catalog`
filters/sorts them deterministically. A database-backed search engine is not useful
for that size. Keep exact service ID access and validated quality/price metadata.

SQLite FTS5 becomes useful for a materially larger curated catalog when lexical
queries over service names/descriptions are demonstrably hard to express as filters.
First collect representative queries and relevance expectations; verify the deployed
SQLite build supports FTS5. A local rebuildable index over public catalog text is
sufficient before considering any separate service. Preserve structured indexes for
merchant/network/asset/price filters as actual queries emerge.

Semantic retrieval becomes useful when users consistently describe needs with
different vocabulary from the catalog and lexical retrieval demonstrably misses
relevant candidates. Evaluate a small labeled corpus, retrieval quality and failure
cases before choosing embeddings. Keep embedding generation and any network/API cost
outside offline CI; use fixtures there. Do not embed keys, signatures, payment
authorizations, private requests or raw transaction evidence.

Both forms of discovery return **candidate service IDs only**. Resolve them against
the current operator catalog, then apply the same grant, purpose, quality, price,
merchant/endpoint, network/token/recipient, reservation and pre-sign checks. Stale,
poisoned, prompt-injected or fabricated search results cannot authorize a purchase.
Record catalog/index version for explanation, not financial authority.

Defer Redis, external search engines, vector databases, embedding services and
distributed queues. They do not close the settlement/recovery gaps and would add
another consistency and credential boundary before a demonstrated workload needs it.

## Validation performed in Phase 3A

Read schema and query source; reproduced the SQL plans and index alternatives in
temporary databases; inspected official SQLite documentation. This is planning
evidence only. The external `check_docs.py` verified all **41 baseline tracked files
byte-for-byte unchanged**, exactly the two requested new Markdown files, eight
relative links/anchors and balanced code fences. `git diff --check` passed, as did
`git diff --no-index --check /dev/null FILE` for each new, untracked document; the
latter checks are necessary because an ordinary diff does not include untracked
files. No credential pattern was found in the documents. The checker recorded 32
distinct external links; it is not a claim that every external URL/anchor was tested.
HEAD remained the accepted SHA. No application tests, wallet operations, external
payment calls or hosted CI were run for this documentation pass.
