# Agentic Payments Lab handoff

Updated 2026-11-12. This is a separately authorized handoff document, written
AFTER the completed read-only consistency review. It does not amend that review,
change code or authorize execution. The original handoff was committed and pushed
as `28bfe29f87386ea51af2e7ae82090db6559fca32`. The worktree was clean at the start
of this maintenance pass.

## Current phase and priority

The bounded generation adapter and its prerequisites are prepared, independently
reviewed, committed and pushed on the branch below. The subsequent architectural
consistency review was read-only and reported findings without fixes. **No new live generation or
payment is authorized.** Earlier historical live usage is distinct from this
adapter-preparation work.

- Adapter review baseline: `e3944fa9f0202c0099f1a06b09038a67b9f3a61e`.
- Published handoff baseline: `28bfe29f87386ea51af2e7ae82090db6559fca32`.
- Branch: `payments/bounded-generation-adapter-offline`.
- Commit: `Prepare bounded generation probe with counted reservations and offline safeguards`.
- Exactly 28 approved source/test/documentation files; no runtime databases or
  evidence directories committed. The adapter and original handoff are included
  in the published branch at `28bfe29`; later maintenance commits are separate.
- Complete the five authorized maintenance tasks before November 17, 2026, each
  in its own commit, then stop. Access ends November 19, 2026. These dates do not
  authorize a probe, waive prerequisites, or imply a scheduled reminder.

## Verified and not verified

Verified offline on the adapter code: 61 focused tests passed in 46.44 seconds;
full validation passed 1,449 tests in 1,606.15 seconds, exit 0. Dependency checks,
Ruff lint/format, repeated simulator demos, evaluator and isolated viewer-server
checks passed. OS network isolation was verified, including subprocess inheritance;
synthetic credentials and mock HTTP exercised the adapter. Browser rendering was
not tested by the validator. Three adapter-related docs changed after the full
suite; tested code/tests were byte-identical at commit. This handoff adds prose
only and has not triggered another suite run.

Verified preservation before commit: 46,135 historical evidence entries plus 2,882
entries from previous offline/review artifacts were unchanged. All 162 pre-adapter
source files, including 66 tests, were copied before adapter edits. Older stopped
journals and the original unresolved reservation were not migrated, retried,
reconciled or released. Commit scope and credential/runtime exclusions were checked.

Not verified: authenticated compatibility of this new adapter, actual account
pricing/invoice deductions, full account inventory, provider enforcement/overshoot,
credit applicability/expiry, real payment settlement/finality, power-loss durability,
independent backup restore, multi-host safety or any guaranteed cost ceiling.
Historical model/count access does not establish those facts. Earlier interrupted
tests are not passes; the final ordered focused/full runs above are the evidence.

## Adapter contract, pricing and actual behavior

Implementation: `src/agentic_payments_lab/buyer_agent/generation_probe_live.py`.
See [adapter details](generation-live-adapter.md) and the separately scoped
[mock preparation](generation-compatibility.md). Do not confuse the minimal probe
with the full paired campaign, which supports bounded tool-result continuation.

At most four POST attempts, in order: OpenAI count, one OpenAI generation, then
Anthropic count and one Anthropic generation only after the first provider passes
the strict gate. Models are `gpt-6-luna` and `claude-haiku-5-5`. Both request 512
TOTAL output tokens including reasoning/thinking, with reasoning `none` and
thinking disabled. Tool schemas remain for compatibility testing but no tool is
executed. No retries, resume, model-read GET, fallback, follow-up turns or payments.
Claims bind the current owner, exact body, endpoint, slot and provider before auth.
Official endpoints/TLS, no proxy/redirect/retry, bounded raw bodies, transient keys
and credential-reflection checks are enforced by the shared wire/exchange.

### Reported rate policy, not placeholder or verified billing

For BOTH providers, the user reports standard short input $0.10/M and output
$0.50/M: **100 input / 500 output nanodollars per token**. These rates are not
independently account-verified. The live adapter does not silently use the mock's
PLACEHOLDER rates.

`reservation_nano = (fresh_bound_input_count + 1024) * 100 + 512 * 500`

The 1,024-token overhead margin is an explicit assumption, NOT a guaranteed input
bound. Output is one bucket; reasoning/thinking is not added again. Count plus
margin must be <=272,000 OpenAI or <=100,000 Anthropic. Unsupported long-context
requests stop BEFORE dispatch. Live generation requires a fresh exact-request
count; the context-window fallback exists only in a separate uncounted estimator
and cannot bypass this gate.

Complete accepted uncached short-mode usage settles at the same reported rates:
`observed_ordinary_input * 100 + observed_total_output * 500` nanodollars. The margin
is not charged as observed usage. Legacy native-rate estimates remain separate
and are not used to release this policy's hold.

No cache configuration is requested. Cache prices are not admitted by this live
policy. Nonzero cache counters, unsupported mode/tier/geography, unexpected
reasoning/thinking or invalid usage stop and prevent complete settlement. Unknown
cache prices are NOT zero and cache writes are NOT ordinary input. Known ordinary
input/output subtotals can be retained; uncertain exposure keeps the reservation
or a greater known subtotal. An unknown category can still have been billed before
it was detected. Public cache-price context is not independently verified account
applicability and is not adopted by this code. `unknown_cost_categories` is a
conservative report flag for any incomplete request, not proof of a specific cache
incident.

### Shared exposure and account-control provenance

Keep the existing $3 aggregate/$1.50-per-provider authorization, one agreed local
authority path/identity, one-shot allocation and exclusive initialization. Live
schema 3 and mock schema 2 are separate; no old journal migration/reset is allowed.
The minimum locally documented prior carry is $0.0098583, ALREADY including the
original unknown $0.001239. Do not add the hold twice or call the carry actual
invoice spend. Breakdown: Nov 3 smoke $0.0004196 (derived estimate), Nov 3 acceptance
$0.0007647, later successful pilot $0.0007724, campaign $0.0066626, plus unknown
$0.001239. These document 108 generation attempts, not exhaustive account usage.
Hash-bound inventory completeness remains an operator attestation.

Historical counts 386/1,121 came from DIFFERENT request bodies. Illustrative
reservations at reported rates are $0.000397/$0.0004705; combined $0.0008675, or
$0.0107258 including prior carry. They are not fresh witnesses for this probe.
At allowed short-count thresholds the conditional reservations are at most
$0.027456/$0.010256, totaling $0.037712. These are arithmetic reservations under
assumptions, not guaranteed provider charges or account-wide spending caps.

User reports only, not independently verified: OpenAI $10 enforced project limit
and $119.99 credits; Anthropic $10 workspace limit and $99.99 promotional credits;
auto-reload off for both. They do not expand the $3 authorization or establish no
overshoot, fees, credit expiration or unrelated account spending.

### Quality and interruption semantics

Only valid final structured output or a supported tool call with valid arguments
passes the strict gate AFTER accepted accounting and successful cleanup. Calls
are evidence only. Refusal, content filtering, truncation, invalid final output,
invalid arguments or unavailable tools stop before the next provider, without
undoing complete accepted accounting. Protocol completion is not useful-output
success. This differs from the older mock-only rehearsal's broader advancement.

Missing/invalid usage, identity/mode/price uncertainty, output >512, count/body
mismatch, budget failure or transport/cleanup failure stops with no retry.
Interruption BEFORE observation leaves pending exposure. Failure AFTER successful
observation preserves completed accounting even if caller/cleanup fails. Original
caller failure and later durable snapshot remain distinct. Stopped/finished owners
cannot restart; no automatic reconciliation or recovery is provided.

## Completed read-only consistency review: all seven topics

Conclusion: no new financial-safety defect found within the documented intact,
same-host SQLite simulator assumptions. The review changed no files and ran no
new tests; it used the saved 1,449-test result. Paths below are relative to the
baseline; line references refer to that commit, not future edits.

1. **Transaction boundaries / atomicity.** Ledger reserve/claim/audit are local
   transactions (`ledger.py:224–380`); payment-authority execution/result and
   merchant cached delivery commit separately (`coordinator.py:66–87`,
   `payments.py:62–106`, `provider.py:120–147`). No cross-file atomic commit exists.
2. **Idempotency / concurrency.** `BEGIN IMMEDIATE`, unique IDs and conditional
   state transitions fence local concurrent claims (`storage.py:37–45`,
   `ledger.py:332–357`, `x402_merchant_store.py:194–244`). This is not multi-host
   fencing or durable deduplication after independent state replacement.
3. **Crash divergence / monotonicity.** Cross-file divergence is expected; terminal
   ledger state remains monotonic (`ledger.py:351–389`). A fourth nonfinancial
   agent-metadata database follows ledger-first binding/recovery
   (`buyer_agent/store.py:49–105`). Independent loss/rollback can invalidate the
   original authority/dedup assumptions (`docs/architecture.md:118–139`).
4. **Reconciliation / liveness.** Simulator reconciliation looks up or cancels,
   never re-executes (`coordinator.py:128–144`). x402 observation/accounting/audit
   commit atomically inside the buyer database (`x402_reconciliation.py:23–35`,
   `x402_store.py:474–558`); finality contradictions freeze, not reverse, state.
   Unknown claims/holds may remain indefinitely pending manual reconciliation
   (`x402_store.py:274–284,358–362`, `x402_merchant_store.py:194–211`).
5. **Guarantees and cancellation boundary.** At-most-once local logical effects
   depend on intact local authority state. The payment cancellation tombstone and
   same-lock ordering (`payments.py:121–135`) cannot be generalized to a remote
   provider. No global exactly-once, fair exchange, guaranteed delivery/refund,
   cross-file backup recovery, adversarial-host protection or cost cap is proved
   (`docs/architecture.md:94–99,118–139,159–165`).
6. **Tests and observability.** Local subprocess/race recovery tests support
   controlled crash windows, not power loss, corruption or restored backups:
   `tests/test_concurrency.py:11–154`, `test_recovery.py:140–170`,
   `test_x402_merchant_recovery.py:281–339`,
   `test_x402_evidence_recovery.py:47–85,160–188,233–274`, and
   `recovery_worker.py:23–25` (`os._exit`). Payment statistics can mix snapshots:
   separate authorization and attempt-count SELECTs in `payments.py:141–152`
   run without one transaction (`storage.py:25–30` autocommit).
7. **Backend / real-provider applicability.** No PostgreSQL backend or PostgreSQL
   isolation/failover tests were found. Provider ASGI fixtures and x402 doubles
   are not evidence of real-provider idempotency, blockchain finality or remote
   cancellation (`provider.py:159–172`). Do not extrapolate SQLite results to PG
   or distributed/real settlement systems.

Code references without a prefix above are under `src/agentic_payments_lab/`.

### Ranked findings and open decisions (not fixed)

- **HIGH for future production/distributed use:** independent database
  loss/rollback breaks dedup assumptions; real-provider cancellation/finality is
  unestablished. These are boundary limitations, not new simulator defects.
- **MEDIUM operational:** unknown holds can remain indefinitely; decide explicit
  operator reconciliation/escalation procedures before a production deployment.
- **LOW concrete observability issue:** statistics read can combine snapshots.
  Decide whether a later separately scoped change should wrap it in one read
  transaction. This handoff does not implement a fix.
- **LOW editorial:** `docs/paired-evaluation.md:3` still says “uncommitted” and
  lines 45–46 broadly deny automatic continuation, while the full paired runner
  has bounded tool-result turns (`buyer_agent/comparison_runner.py:310–343`).
  The minimal adapter genuinely has none. These were nonblocking review findings;
  the consistency review made no documentation changes.

Open before any live probe: explicit execution approval; complete prior-inventory
reconciliation; chosen shared authority identity/path; current source and body
hash review; preflight-lineage acceptance; reported-rate/margin acceptance;
uncached-mode residual-risk acceptance or resolution of missing pricing/control.
Code review fields are trusted operator assertions, not proof of user consent.
No fresh real authority or credential loading is needed to finish this handoff.

## Evidence and reproducible inspection

Local evidence is outside Git and may be ephemeral. Do not delete, migrate, open
old DBs through initialization, or overwrite stopped records. Arrange an authorized
transfer/retention of useful sanitized evidence before access ends; no such transfer
is performed by this document.

Current adapter evidence directory:
`/tmp/payments-probe-adapter-offline-o3xymo_a/`

- `adapter-focused-ordered.log`: 61 passed; SHA-256
  `1203c6b4fb59d4b23410f25b6c3d64f22a160c1d368f951534d0beff5d85a8f9`.
- `full-validator-ordered.log`: 1,449 passed; SHA-256
  `4000c6d2731281cbe156dff353fe1858359d280b0e1efc3cce3ec15ac2b8f13b`.
- Both corresponding `.exit` files are 0. `commit-ordered.log/.exit` records the
  28-file commit. `precommit-historical.log` and `precommit-source-artifacts.log`
  record preservation and unchanged tested code. `before/` and
  `before-source-manifest.json` preserve all pre-adapter source/test files.
- Historical exact-body packet: `/tmp/payments-live-probe-review-6c8584jp/REVIEW.md`,
  SHA-256 `c91257e182243de2a91b221151f37bfc028d7af9d4421f35a2be8c7a24dc88b1`.
  Its JSON bodies remain useful references; its mock/PLACEHOLDER implementation
  status is historical. Use the committed adapter policy above for current rates
  and strict gate, not that packet's old policy prose.
- Earlier non-generating diagnostic:
  `/tmp/payments-endpoint-diagnostic-4_d266j_/FINAL-SUMMARY.json`. Six verified
  responses, zero generation attempts; count/model access is not billing identity.

### Read-only inspection examples (no tests or provider access)

```bash
cd /home/alvaro/Projects/Portfolio/Agentic-Payments-Lab
git status --short
git branch --show-current
git show --stat --oneline e3944fa9f0202c0099f1a06b09038a67b9f3a61e
git show e3944fa9f0202c0099f1a06b09038a67b9f3a61e:src/agentic_payments_lab/buyer_agent/generation_probe_live.py
grep -E '[0-9]+ passed' /tmp/payments-probe-adapter-offline-o3xymo_a/adapter-focused-ordered.log /tmp/payments-probe-adapter-offline-o3xymo_a/full-validator-ordered.log
sha256sum /tmp/payments-probe-adapter-offline-o3xymo_a/adapter-focused-ordered.log /tmp/payments-probe-adapter-offline-o3xymo_a/full-validator-ordered.log
```

### Offline test reproduction (WRITES temporary fixtures/logs; not read-only)

These are reference commands for a separately authorized test run, NOT commands
executed during handoff authoring or consistency review. Inspect the existing
isolation launcher first. It uses the pinned runtime, a cleaned environment and
an inherited new network namespace with loopback only. It must fail closed if
isolation is unavailable. Do not substitute a merely offline dependency flag for
network isolation, and never load `.env` or real credentials.

```bash
cd /home/alvaro/Projects/Portfolio/Agentic-Payments-Lab
TEST_TMP=$(mktemp -d /tmp/payments-handoff-tests-XXXXXXXX)
python3 /tmp/payments-probe-adapter-offline-o3xymo_a/isolated.py   .runtime/venv/bin/python -B -m pytest -x -q   -o cache_dir="$TEST_TMP/cache" --basetemp="$TEST_TMP/fixtures"   tests/test_generation_probe.py tests/test_generation_probe_live.py
# Only if focused tests pass and a full run is authorized:
python3 /tmp/payments-probe-adapter-offline-o3xymo_a/isolated.py   .runtime/venv/bin/python -B scripts/validate_offline.py   --parent-netns "$(readlink /proc/self/ns/net)" --uv /home/alvaro/.local/bin/uv
```

These commands write new temporary databases and caches; they do not initialize
or open historical retained authorities. No live execution command is supplied
or represented as authorized. If the temporary launcher/evidence is unavailable,
stop and re-establish the documented isolated setup rather than bypass it.

## Recommended next action

Complete only the five authorized maintenance tasks before November 17: correct
status, retain the isolation launcher in `scripts/`, preserve sanitized evidence,
fix the LOW editorial finding and add a short "Start here" section. Keep each task
in its own commit, then stop. Do not change adapter code or prepare a live probe.
Keep the remaining ranked findings and execution prerequisites visible. The
deadline does not authorize credential loading, exposure resets or live execution.
