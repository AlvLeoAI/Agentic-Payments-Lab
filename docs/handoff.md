# Agentic Payments Lab handoff

## Start here

- Read the [current status and scope](#current-phase-and-priority).
- Distinguish [fresh evidence from lost historical records](#evidence-and-reproducible-inspection).
- Use the [offline reproduction procedure](#offline-test-reproduction-writes-temporary-fixtureslogs-not-read-only) only for an authorized run:
  ask the user in chat before each run, offline pytest included (~30 min, writes temporary fixtures).
- Review the [remaining findings and editorial correction](#ranked-findings-and-current-disposition).
- Stop after the scoped maintenance: no live probe, real payments or deployment.
  "Stop" means: make no further commits or changes outside a task the user gives in
  chat. Claude Code sessions also load the hard rules in the repository-root `CLAUDE.md`.

Updated 2026-11-13 (maintenance 2026-11-12; Claude Code onboarding, `.venv`
removal, and the PR #1 credential-reflection fix with its revalidation
2026-11-13). This is a separately authorized handoff document, written
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

- Adapter commit, also the review baseline: `e3944fa9f0202c0099f1a06b09038a67b9f3a61e`
  (`Prepare bounded generation probe with counted reservations and offline safeguards`).
- Published handoff baseline: `28bfe29f87386ea51af2e7ae82090db6559fca32`.
- Branch: `payments/bounded-generation-adapter-offline`, tracking `origin`. If
  `git status -sb` shows no upstream, compare `git rev-parse HEAD
  origin/payments/bounded-generation-adapter-offline` instead.
- Exactly 28 approved source/test/documentation files; no runtime databases or
  evidence directories committed. The adapter and original handoff are included
  in the published branch at `28bfe29`; later maintenance commits are separate.
- Git file counts: 144 tracked files before the adapter (`e3944fa^`), plus 26
  added and 2 modified by the adapter commit = 170, plus `docs/handoff.md` = 171.
  The historical "162 pre-adapter source files" claim below does not match Git's
  144 and cannot be rechecked; treat it as unverified.
- **All five maintenance tasks are done**, one commit each: `e2edfd9` (status),
  `6a374a1` (validator procedure), `799b4f6` (evidence), `419bda6` (editorial
  finding), `6f275ab` (Start here). The local
  `origin/payments/bounded-generation-adapter-offline` ref showed `6f275ab` on
  2026-11-12. After the five tasks, a separately requested Claude Code onboarding
  pass added `CLAUDE.md` and `.claude/settings.json` and closed cold-start gaps in
  this file. It is not one of the five tasks, and it is committed only if `git log`
  shows it. Do not infer HEAD from this document: run `git status -sb` and
  `git log -3`. `git diff --stat 6a374a1..6f275ab` touches only `docs/handoff.md`
  and `docs/paired-evaluation.md`, which supports the documentation-only claim.
- **One code change after the adapter commit:** `cb91e9c` (2026-11-13), requested
  by the user from the PR #1 review. Final-output text and OpenAI tool arguments
  are JSON carried inside a string; a credential escaped at that inner level passed
  the raw and first-decode reflection checks and reached durable evidence.
  `ProviderWire.check_decoded` (`comparison_wire.py`) now rejects the exact key in
  the fully decoded response and evidence record before retention, called from the
  shared exchange (`comparison_runner.py`). The finding named the paired runner;
  the fix also adds the same three-line override to `comparison_live.py` and
  `generation_probe_live.py`, because the generation adapter inherits that exchange
  path and had the same gap. Five regression tests use synthetic keys only. Other
  encodings (base64, URL escapes) are not decoded anywhere and are not checked.
  This changes the reviewed source hash, so any earlier source review no longer
  matches the code.
- The maintenance window was: finish the five tasks before November 17, 2026, each
  in its own commit, then stop. The user's Codex/OpenAI subscription access ends
  around November 19, 2026; the repository and evidence directory remain. These dates do not
  authorize a probe, waive prerequisites, or imply a scheduled reminder.

## Verified and not verified

Historical reports stated that 61 focused tests passed in 46.44 seconds and
1,449 full-suite tests passed in 1,606.15 seconds. Those original logs are now
lost and their recorded hashes cannot be verified. Their reported results remain
historical claims, separate from the fresh evidence below.

Fresh local validation on 2026-11-12 at
`6a374a1a9e00168564391b22f674241076dd9247`: **1,449 tests passed in 1,722.54
seconds**; the full validator exited 0 in 1,770 seconds overall. Lockfile checks,
Ruff lint/format, repeated authority-checked simulator/agent demos, four evaluator
cases and the viewer-server check passed. OS network isolation and subprocess
inheritance were verified. Only synthetic test credentials and mock provider HTTP
were used; no real credentials or external network. Browser rendering and hosted
CI were not tested. All 171 tracked files matched the source snapshot before and
after the run; the edits from `6a374a1` through `49953bf` are documentation and
Claude Code configuration only.

**Latest verified commit:** fresh local validation on 2026-11-13 at
`cb91e9c35231e4e6237ed00eebbc42bf55e0d671`, after the credential-reflection fix:
**1,454 tests passed in 1,578.16 seconds** (the 1,449 above plus 5 regression
tests); the full validator exited 0 in 1,619 seconds overall, with the same checks,
isolation and synthetic-only inputs as the 2026-11-12 run. All 173 tracked files
matched the source snapshot before and after the run. Hosted CI and browser
rendering are not part of this local evidence. To confirm that nothing but
documentation changed since, run `git diff --stat cb91e9c..HEAD`.

Historical precommit reports also claimed preservation of 46,135 historical entries,
2,882 previous offline/review entries and 162 pre-adapter source files, including
66 tests. The supporting logs and copies are unavailable, so that preservation
cannot be reverified. This maintenance did not open or alter retained authorities,
retry stopped operations, reconcile or release exposure.

Not verified: authenticated compatibility of this new adapter, actual account
pricing/invoice deductions, full account inventory, provider enforcement/overshoot,
credit applicability/expiry, real payment settlement/finality, power-loss durability,
independent backup restore, multi-host safety or any guaranteed cost ceiling.
Historical model/count access does not establish those facts. Earlier interrupted
tests are not passes. The fresh run does not recover the lost logs or re-establish
historical provider access, account usage or preservation claims.

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
Claims bind the current owner (the run's durable exclusive owner record), exact
body, endpoint, slot (the request's position in the fixed attempt schedule) and
provider before auth.
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

Keep the existing $3 aggregate/$1.50-per-provider authorization (AGENTS.md's
"per-arm"; each arm is one provider), one agreed local
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
new tests; it used the then-saved 1,449-test result, whose log is now lost (the
fresh 2026-11-12 run reproduces the count but is a different record). Paths below
are relative to the adapter baseline `e3944fa`; `28bfe29` added only
`docs/handoff.md`, so source line references are identical at both commits.

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

### Ranked findings and current disposition

- **HIGH for future production/distributed use:** independent database
  loss/rollback breaks dedup assumptions; real-provider cancellation/finality is
  unestablished. These are boundary limitations, not new simulator defects.
- **MEDIUM operational:** unknown holds can remain indefinitely; decide explicit
  operator reconciliation/escalation procedures before a production deployment.
- **LOW concrete observability issue:** statistics read can combine snapshots.
  The user decides whether a later separately scoped change should wrap it in
  one read transaction. This handoff does not implement a fix.
- **Credential reflection through nested JSON — fixed in `cb91e9c`:** found by the
  PR #1 review, not by the consistency review above. See the
  [current phase](#current-phase-and-priority) entry; revalidated at that commit.
- **LOW editorial — corrected in this maintenance:** `docs/paired-evaluation.md`
  now states that the preparation is committed and distinguishes the full paired
  runner's bounded tool-result turns from the minimal adapter's lack of follow-up
  turns. This corrects the two nonblocking prose findings; implementation is
  unchanged. The original consistency review itself made no documentation changes.

Open before any live probe: explicit execution approval; complete prior-inventory
reconciliation; chosen shared authority identity/path; current source and body
hash review; preflight-lineage acceptance; reported-rate/margin acceptance;
uncached-mode residual-risk acceptance or resolution of missing pricing/control;
confirming the probe runs on the pinned `.runtime/` interpreter, the only runtime
that satisfies the SQLite WAL-reset prerequisite ([phase3b7](phase3b7.md)). System SQLite remains unpatched, and other
runtimes are not accepted ([phase3b1](phase3b1.md#sqlite-prerequisite--live-use-blocked)).
The old system-Python `.venv` was removed locally on 2026-11-13; a bare
`uv run`/`uv sync` would recreate it, so always select
`UV_PROJECT_ENVIRONMENT=.runtime/venv`.
Code review fields are trusted operator assertions, not proof of user consent.
These terms (authority identity/path, preflight lineage, review fields, arms) are
defined in [generation-live-adapter.md](generation-live-adapter.md) and
[generation-compatibility.md](generation-compatibility.md).
No fresh real authority or credential loading is needed to finish this handoff.

## Evidence and reproducible inspection

Durable local evidence is outside Git in two dated directories under
`/home/alvaro/lab-evidence/agentic-payments-lab/`:
`2026-11-12-handoff-maintenance/` (run at `6a374a1`) and
`2026-11-13-credential-reflection-fix/` (run at `cb91e9c`, the latest verified commit).
Do not delete, migrate, initialize old databases or overwrite stopped records.
Read-only inspection of these directories (the commands below) is allowed.
`sha256sum --check SHA256SUMS` was re-run on 2026-11-13: all 15 listed files OK in
the first directory and all 8 in the second, exit 0.

### Lost historical evidence — recorded hashes are unverifiable

The named `/tmp` directories are absent. Searches of `/tmp`, `/var/tmp` and
readable home-directory locations found no surviving copies of the named artifacts.
Search scope and inaccessible system-private directories are recorded in
`evidence-search.json` and `home-evidence-search*`. No original execution logs were
copied or hash-verified. These recorded hashes are retained only for traceability:

- `/tmp/payments-probe-adapter-offline-o3xymo_a/adapter-focused-ordered.log`:
  **lost; recorded SHA-256 unverifiable**
  `1203c6b4fb59d4b23410f25b6c3d64f22a160c1d368f951534d0beff5d85a8f9`.
- `/tmp/payments-probe-adapter-offline-o3xymo_a/full-validator-ordered.log`:
  **lost; recorded SHA-256 unverifiable**
  `4000c6d2731281cbe156dff353fe1858359d280b0e1efc3cce3ec15ac2b8f13b`.
- The associated `.exit` files, `commit-ordered.log/.exit`,
  `precommit-historical.log`, `precommit-source-artifacts.log`, `before/` and
  `before-source-manifest.json` are also missing; their previous claims cannot
  be rechecked against those artifacts.
- `/tmp/payments-live-probe-review-6c8584jp/REVIEW.md`:
  **lost; recorded SHA-256 unverifiable**
  `c91257e182243de2a91b221151f37bfc028d7af9d4421f35a2be8c7a24dc88b1`.
  Its historical body packet is unavailable and was not recreated.
- `/tmp/payments-endpoint-diagnostic-4_d266j_/FINAL-SUMMARY.json`: **lost**;
  no hash was recorded here. The previously reported six verified responses and
  zero generation attempts cannot be reverified from the missing summary.

### Fresh evidence — not a replacement for the original records

- `full-validator.log`: the single fresh run described above; SHA-256
  `384fa5a419d0337586ce37859b9b8b1dd10b11f5dc88b8a9df1cceecb5f1ec1b`.
- `full-validator.exit`: 0. `fresh-validation.json` records observed results;
  `validation-provenance.json`, `validation-command.txt`, UTC start/finish files
  and `tracked-content-before-validation.json` identify the command and inputs.
- `historical-handoff-28bfe29.md` is a surviving committed report copied from Git
  with byte-for-byte SHA-256 verification. It is not an original execution log.
- `evidence-inventory.json` distinguishes the missing originals from the surviving
  document. `SHA256SUMS` verifies the retained files in this dated directory.

In `2026-11-13-credential-reflection-fix/`, same single-run procedure at `cb91e9c`:

- `full-validator.log`: SHA-256
  `091c082cac5004b4281c0a0b25b9a98d8edb5bd1060dbcb041fc6306f62111cf`;
  `full-validator.exit`: 0.
- `fresh-validation.json`, `validation-provenance.json`, `validation-command.txt`,
  UTC start/finish files and `tracked-content-before-validation.json` (173 files)
  record the results, command and inputs. `SHA256SUMS` covers these 8 files.

Fresh passing tests do not validate the lost historical logs or authorize a live
probe. No real credentials, wallets, real payments or live-probe preparation were used.

### Read-only inspection examples (no tests or provider access)

```bash
cd /home/alvaro/Projects/Portfolio/Agentic-Payments-Lab
git status --short
git branch --show-current
git show --stat --oneline e3944fa9f0202c0099f1a06b09038a67b9f3a61e
git show e3944fa9f0202c0099f1a06b09038a67b9f3a61e:src/agentic_payments_lab/buyer_agent/generation_probe_live.py
handoff_evidence=/home/alvaro/lab-evidence/agentic-payments-lab/2026-11-12-handoff-maintenance
rg '[0-9]+ passed' "$handoff_evidence/full-validator.log"
(cd "$handoff_evidence" && sha256sum --check SHA256SUMS)
fix_evidence=/home/alvaro/lab-evidence/agentic-payments-lab/2026-11-13-credential-reflection-fix
rg '[0-9]+ passed' "$fix_evidence/full-validator.log"
(cd "$fix_evidence" && sha256sum --check SHA256SUMS)
```

### Offline test reproduction (WRITES temporary fixtures/logs; not read-only)

Use the committed `scripts/validate_offline.py` with the Linux `unshare` procedure
in [README validation instructions](../README.md#validation). The former temporary
`isolated.py` launcher is unavailable and is not required or recreated. The clean
environment below excludes inherited API keys and proxy configuration. The
validator verifies the new loopback-only namespace and subprocess inheritance
before running any checks. Isolation failure must stop execution; never substitute
an offline dependency flag for network isolation or load `.env` or real credentials.

```bash
cd /home/alvaro/Projects/Portfolio/Agentic-Payments-Lab
parent_netns=$(readlink /proc/self/ns/net)
uv_executable=$(command -v uv)
env -i HOME="$HOME" LANG="${LANG:-C.UTF-8}" PATH="$PATH" \
  PYTHONDONTWRITEBYTECODE=1 \
  unshare --user --map-root-user --net -- \
  .runtime/venv/bin/python -B scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$uv_executable"
```

These commands write new temporary databases and caches; they do not initialize
or open historical retained authorities. No live execution command is supplied
or represented as authorized. If the pinned runtime, dependencies or namespace
support are unavailable, stop rather than bypass isolation. The validator manages
temporary fixtures and caches outside the repository. Retain its output in a new
dated directory under `/home/alvaro/lab-evidence/agentic-payments-lab/`, never in
an existing one; both existing directories are read-only.
The validator sets `UV_PROJECT_ENVIRONMENT` to its own interpreter prefix
(`.runtime/venv`), so the `env -i` command above does not need to pass it.

## Recommended next action

The five maintenance tasks are complete (see [current phase](#current-phase-and-priority)).
The one code change since (`cb91e9c`) was a user-requested fix from the PR #1
review and is revalidated. There is no authorized next task. Wait for the user. Do
not change adapter code or prepare a live probe without a new request. Keep the remaining ranked findings and execution
prerequisites visible. No date authorizes credential loading, exposure resets or
live execution.

Open decision for the user: `main` (`b199090`) ends at the Luna evaluation baseline
(Phase 3B.8 and the viewer). Everything from `e3944fa` onward (paired comparison,
generation adapter, this handoff and its maintenance) is only on
`payments/bounded-generation-adapter-offline`, a fast-forward of `main`. Pull request
[#1](https://github.com/AlvLeoAI/Agentic-Payments-Lab/pull/1) was opened on
2026-11-13 with the user's approval and is under review. Merging it is the user's
decision: do not merge it, and do not push to it, without approval.
