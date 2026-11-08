# Phase 3B.7 — isolated runtime and offline simulator pilot preparation

Built on accepted `bcaa94c0c2a8baa4abb673f3db25ac58450f0832`. This slice is
uncommitted preparation, not a live pilot. No credentials, model-access probes,
OpenAI requests, real payments, external merchant/RPC/facilitator calls, deployment,
system-package changes or retained-database migrations are part of this work.

## Selected runtime and provenance

The supported route is one pinned **Linux x86_64** Astral Python Build Standalone
distribution, installed under ignored `.runtime/`. The installer verifies its
archive checksum before extraction and its executable checksum before execution;
it then checks the actual Python-linked SQLite version/source ID and built-in module
origin. It rejects an existing installation with different bytes and never overwrites
it. This is binary/vendor provenance verification, not an independent reproducible
build or evidence that all SQLite defects are absent.

| Item | Pinned value |
| --- | --- |
| Vendor release | [Astral 20261026](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026) |
| Archive | `cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz` |
| Archive SHA-256 | `ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20` |
| Actual Python | 3.12.16, Clang 23.1.3 build dated 2026-10-26 |
| Executable SHA-256 | `23bc148d2b48964b621c5561ff2b81c1d9c7b717967a7491efbf12f4304882b5` |
| Actual SQLite | 3.53.1, statically embedded in that executable (`_sqlite3` is built-in) |
| SQLite source ID | `2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9` |

The archive hash was independently matched against the release asset digest and
the release's `SHA256SUMS`. The vendor's pinned
[build dependency manifest](https://github.com/astral-sh/python-build-standalone/blob/20261026/pythonbuild/downloads.json)
selects the upstream SQLite 3.53.1 source. The loaded source ID agrees with its
[upstream release record](https://www.sqlite.org/releaselog/3_53_1.html), which includes
the WAL-reset fix; SQLite also documents fixed versions in its
[WAL-reset explanation](https://www.sqlite.org/wal.html#the_wal_reset_bug).
The earlier Python 3.12.13/20260414 candidate was checksum-valid but embedded
SQLite 3.50.4 and was rejected. A current-looking Python label alone is insufficient.

The old `.venv` remains Python 3.12.3 linked to system SQLite 3.45.1, library SHA-256
`85265a9d4afca6f4b325ceb078b669c754fb881abed4cafe91ccebe9d625d975`.
It has **not** been patched or replaced. Only the selected runtime resolves this
specific prerequisite. Other interpreters, operating systems, ARM64/AWS, retained
state and live integrations have not been accepted. Passing tests do not themselves
prove the WAL-reset fix.

Install explicitly before isolating execution (uv remains pinned to 0.11.7):

```bash
python3 scripts/prepare_runtime.py
UV_PROJECT_ENVIRONMENT=.runtime/venv uv sync --locked --no-python-downloads \
  --python .runtime/cpython-3.12.16-20261026/bin/python3.12
parent_netns=$(readlink /proc/self/ns/net)
env -u OPENAI_API_KEY -u OPENAI_ORG_ID -u OPENAI_PROJECT_ID \
  PYTHONDONTWRITEBYTECODE=1 \
  unshare --user --map-root-user --net -- \
  .runtime/venv/bin/python scripts/validate_offline.py \
  --parent-netns "$parent_netns" --uv "$(command -v uv)"
```

`--archive PATH` permits installing the same checksum-pinned downloaded archive.
No shell startup file, global PATH, system package, default `.python-version`, old
virtual environment, dependency definition or lock pin is changed. The full validator
checks the selected runtime as well as namespace isolation; subprocesses inherit
both the isolated namespace and chosen virtual environment. CI is configured to
install this same artifact before isolation; a hosted run is still pending publication.

## Supported entry point and authority

`initialize_pilot(directory, goal=..., grant=..., task_id=...,
estimate_ceiling_nano_usd=...)` is an explicit trusted-host operation. It first calls
`require_pilot_runtime()`, before validating arguments or opening/creating any pilot
state. It then requires a **new directory**, creates ordinary simulator state, binds
the explicit operator grant/task/goal with the existing initialization contract,
and exclusively creates its `pilot-budget.sqlite3`. Existing or partial directories
are rejected. An interrupted multi-file initialization can leave partial files;
preserve and inspect them, do not automatically repair, reuse or replace that pilot.
This is not an atomic cross-database initialization or backup framework.

The result contains the runtime, BuyerSession, AgentStore, Goal and CallBudget for
`run_pilot_preparation(...)`. That function accepts only `mode="offline"`, checks
the runtime and immutable task/grant/purchase/goal-derived batch ID, then invokes
the existing four-tool runner over its in-memory Responses transport. There is no
live transport option, fallback, secret lookup or new CLI backend. `OfflineX402Buyer`
is rejected and remains scripted-only. All financial authority remains in the
existing simulator stores; the call-budget file owns API planning evidence only.

`pilot_limits()` and `pilot_preflight()` also verify the runtime. They do not undo
work that a caller previously performed through generic legacy APIs. Caller-created
Python sessions, model objects and database administration remain trusted host
capabilities; this is not a global sandbox around every Runtime/CallBudget caller.
The legacy live simulator CLI has not gained these safeguards and is **not** the
supported route for a later pilot. The default simulator/four tools are unchanged.

## Sourced pricing and actual usage shape

`LunaRates` is immutable and identifies the direct OpenAI Standard/default rate card
verified on **2026-11-07**. Sources are the official
[pricing page](https://developers.openai.com/api/docs/pricing),
[Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna), and
[cache usage schema](https://developers.openai.com/api/docs/guides/prompt-caching#monitor-cache-performance).
It covers text/function-call Responses with global processing and at most 272,000
input tokens; regional premiums, alternate tiers, long-context multipliers and
hosted tools are not silently priced with this card.

| Disjoint category | USD per million tokens | USD nanounits per token |
| --- | ---: | ---: |
| Ordinary input | 0.10 | 100 |
| Cache reads | 0.01 | 10 |
| 30-minute cache writes | 0.125 | 125 |
| 12-hour cache writes | 0.25 | 250 |
| Output, including reasoning | 0.50 | 500 |

Input minus reads minus aggregate writes is ordinary input. The documented
`cache_write_tokens_details.ttl_30m_tokens` and `ttl_12h_tokens` must be nonnegative
integers and sum to aggregate writes. Reads and writes must not overlap. Each write
is charged once at its lifetime's total rate; aggregate writes are not added again.
Reasoning is a subset of output, not an additional charge.

OpenAI 3.42.1's generated usage type lacks the lifetime fields, but it preserves
them as extra fields. The transport parses the raw serialized response before SDK
conversion, so neither lifetime is lost. HTTP mocks test both this raw path and
the pinned SDK's round trip. The old Phase 3B.6 synthetic fixtures remain supported
under their separate synthetic card; they are not accepted as complete sourced-rate
usage. No dependency upgrade is needed.

Missing/malformed/unknown categories, missing lifetime details, incorrect totals,
model/tier mismatch, or unexpected price assumptions retain the reservation and
stop continuation. Individually known token categories survive; unknown totals/cost
remain null. A long-context observation is explicitly unpriced and blocked rather
than attributed the short-context rate. Returned identity must be exactly the
requested model and default tier. The price snapshot must be revalidated before
any later live approval; it is not an assertion of future provider prices or billing.

## Bounds, interruption and future preflight

The preparation fixes `gpt-6-luna`, reasoning `none`, tier `default`, and permits
only lower limits than three invocations, six actual requests/SDK turns each,
18 total requests, 512 output tokens/request, eight tool attempts and a 30-second
cooperative timeout. Calls are sequential and both SDK layers have zero retries.
The whole serialized body, including tools, output schema and history, is at most
32 KiB. Unsupported request options are rejected before reservation/dispatch.

Each invocation and request uses the existing durable journal and exclusive owner.
The reservation commits before the mock responder can run. Its conservative plan
uses body bytes plus 1,024 framing tokens, the full 12-hour write rate, and maximum
output. For the maximum 18-request configuration this is **$0.156672**; using
ordinary input instead is **$0.0654336**. These are byte-derived planning estimates,
not exact token bounds or a guaranteed provider-side billing cap.

The proposed future **$0.25** ceiling is **not authorized spending**. Test fixtures
may use that number only as offline planning data. Preflight returns
`live_authorized=false`, unverified access/credit flags and no billing-cap guarantee.
No credit balance, expiry, project model access or credentials were inspected.

Cancellation/timeout and process exit preserve the original journal ownership and
unresolved exposure. Known partial usage stays durable; repeated calls cannot reset
the batch or resume an incomplete invocation. Payment/delivery remain authoritative
after a model error, with no financial cleanup, release, reconciliation or repayment.
Cleanup is best effort and can fail under repeated cancellation, storage errors,
process death or blocking I/O. Timeout is cooperative. Restoring an older complete
history, replacing/deleting batches, mixed-version writers and hostile trusted host
code remain outside the local journal's guarantees.

New sourced-policy files reject in the Phase 3B.6 binary because its strict policy
does not understand this rate-card source. Existing synthetic policies remain readable
by the new code. No retained file is migrated and no financial schema changes.

Before a live simulator pilot: obtain separate approval for the activation change,
exact scenario/counts and spending ceiling; recheck this runtime and current rates;
verify intended project/model access and applicable pricing without assuming credit;
create fresh explicitly bound state; and review the guarded transport change.
The preparation cannot be made live by passing a key or changing a mode flag.
No wallet/testnet/x402 live-model acceptance follows from this work.

## Validation evidence

Deterministic coverage exercises the actual pinned SDK's serialized continuation
requests, cache categories, identity and price assumptions, pre-dispatch commits,
request/invocation limits, cancellation before/after a simulated payment, timeouts,
process exits, restart fences, fresh-state initialization and refusal before writes.
Charge assertions query the payment-authority database. Subprocess tests retain full
stdout/stderr diagnostics; double counters are never described as chain charges.

The selected-runtime validation exposed an inherited late-response test's timing
assumption: its 100 ms runner deadline could expire before the challenge checkpoint.
Task-outcome diagnostics confirmed timeout with no claim, signing, dispatch or
reserved/spent amount. That test now expires a real asyncio timeout only after its
target checkpoint, still requiring a late response that swallows cancellation and
zero authority calls. A separate regression exercises the unmodified runner's early
deadline without waiting for a later checkpoint. Application timeout behavior is
unchanged; the original failed run and before/after probe evidence are retained.
The payment-authority inspection helper explicitly closes its SQLite connection
before taking state snapshots. A held-reference regression verifies that closure;
initialization refusal still preserves every file and byte, including after garbage
collection. WAL/SHM disappearance from delayed test-reader cleanup is not attributed
to an application migration or ignored by the preservation assertions.

The final validation report is retained outside the repository. Local checks and
any future hosted workflow results must be reported separately. No hosted Phase 3B.7
run or live pilot has been performed.
