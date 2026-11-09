# Local historical pilot viewer

This viewer presents the separately authorized Phase 3B.8 simulator pilot recorded
on 2026-11-08. It provides one page with purchase, replay and refusal tabs. All
payments are simulated; SIM-USDC has no monetary value. There is no agent runner,
database connection, payment control, reconciliation, retry, chat, upload, credential
entry, arbitrary file selector, deployment or live-model activation in the viewer.

The charcoal and warm-gold theme uses Streamlit's native configuration and layouts,
with 16px body text and no custom CSS, unsafe HTML, remote fonts or JavaScript. The
takeaway at the top reads **3 scenarios passed · 1 simulated charge · 10 model
requests**. Compact purchase/replay/refusal cards remain outside the detail tabs;
switching tabs never removes the other scenario summaries. These are ordinary page
content, not a sticky overlay. The large detail heading explicitly says **Simulated
payment: settled** for purchase/replay and **Simulated payment: not started** for
refusal. Delivery remains separate. Atomic units and tool outcomes are in collapsed
evidence sections. The footer is labelled **Fresh run totals**, distinct from the
selected scenario's request/token counts. Unknown usage and the unresolved original
reservation remain visible and unchanged.

## Saved facts and uncertainty

The only runtime data source is the bundled `pilot_viewer/pilot-v1.json` file.
Its version-1 schema rejects extra fields, coercions, unknown states, nonfinite
numbers, duplicate JSON keys, inconsistent arithmetic and oversized collections.
Reads are bounded to 32 KiB plus one overflow byte. Invalid or missing data produces
an unavailable-data message without metrics or fabricated successful results.
Rejected values, parser details and source exception text are not displayed.

The snapshot was curated from the retained sanitized summary and three scenario
reports. The public snapshot includes their SHA256 hashes, the earlier tested base
commit and source-manifest hash, and the later publication commit. These identify
the source history; they do not authenticate edited data or prove chain facts.
The earlier live pilot ran against reviewed uncommitted source, before later
documentation closure and publication. Original databases, WAL files and reports
are never opened by the viewer. Treat the bundled file and local Python installation
as trusted operator inputs; this is not a sandbox against a filesystem administrator.

| Fresh scenario | Requests | Tools | Input / output tokens | Estimated API cost |
| --- | ---: | ---: | ---: | ---: |
| Purchase | 5 | 4 | 3557 / 154 | USD 0.0004327 |
| Replay | 2 | 1 | 1133 / 45 | USD 0.0001358 |
| Refusal | 3 | 2 | 1734 / 61 | USD 0.0002039 |

Fresh totals are 10 requests, 6424 input and 260 output tokens, and USD 0.0007724
estimated API cost. Saved ordinary input/output rates are 100/500 nanoUSD per token;
all observed cached/read/write/reasoning counts in this fresh attempt are zero.
This is a historical estimate, not an invoice, a current price lookup or a billing cap.

Each saved financial snapshot is cumulative: one execution, authorization and
simulated charge of 10000 atomic units. The viewer uses the final cumulative total
once and derives scenario charge deltas of 1, 0, 0. It does not sum three repeated
charge snapshots. Purchase and replay share `purchase-1`; refusal has no purchase
row and displays no purchase alias. Payment and delivery states are separate.
Tool traces contain only allowlisted host event names, sequence numbers and outcomes.
No prompts, model explanations, raw responses, request bodies/headers, receipt
payloads, credentials or local source paths are included.

All ten fresh responses omitted the whole cache-write lifetime-details object.
Observed lifetime counts remain null/unknown. Separately labelled zero allocations
were derived from the explicit integer-zero aggregate; no observed zero is invented.
The earlier stopped request retains 510 observed input and 15 observed output tokens,
incomplete usage accounting, an unknown accepted cost estimate and its unchanged
USD 0.001239 reservation. That reservation is not added to the fresh estimate as a
billed total. No history is repriced, released, reconstructed or reset. Displaying
historical requests or amounts grants no authorization for further calls.

## Supported local launch

Use the existing verified Linux x86_64 Python 3.12.16 / SQLite 3.53.1 runtime.
The old `.venv` and system SQLite are not replaced or remediated. Streamlit is pinned
to [1.67.0](https://pypi.org/project/streamlit/1.67.0/) in the project dependencies;
the lock adds 15 packages without changing any existing external package lock entry.
Install dependencies before runtime network isolation:

```bash
UV_PROJECT_ENVIRONMENT=.runtime/venv uv sync --locked --no-python-downloads \
  --python .runtime/cpython-3.12.16-20261026/bin/python3.12
.runtime/venv/bin/python -I -B scripts/view_pilot.py
```

Open `http://127.0.0.1:8501`. `--port 8502` selects another local port; there are no
host, snapshot-path, credential or execution arguments. Stop with Ctrl+C. The
launcher also handles SIGTERM and attempts bounded child-process cleanup. Abrupt
SIGKILL or repeated interruption can defeat cleanup; it does not mutate financial
state or justify any recovery/payment action.

Do not source `.env`, invoke `streamlit run` directly from the repository, or use
the old interpreter as a substitute. The supported launcher checks the runtime
before starting Streamlit. It creates a temporary profile, working directory and
staged entrypoint outside the repository. The Streamlit child receives a minimal
environment, with no inherited credentials, proxies, PYTHONPATH or STREAMLIT_* values.
Its temporary HOME is only a child-process configuration root; the parent environment
and real home are untouched. Caches and temporary files use that disposable context.

Streamlit can read configuration and secrets from global HOME, CWD **and the main
script directory**, as described in its [configuration documentation](https://docs.streamlit.io/develop/concepts/configuration/options).
The staged entrypoint addresses the third location. A fresh fixed config disables
telemetry, file watching, automatic source reruns, static serving and developer menu
controls, and sets `secrets.files=[]`. CORS and XSRF remain enabled. Snapshot text
uses plain text or constrained values; no unsafe HTML or custom JavaScript is used.

These are configuration and interface boundaries, not authentication or OS egress
isolation. The normal launcher binds only IPv4 loopback but does not deny outbound
network syscalls. Do not expose it through a public bind, proxy or deployment.
Framework localhost/WebSocket traffic is expected; the viewer has no background
data polling or external-client calls. OS-isolated tests establish a separate,
stronger no-external-network boundary for validation, not for every possible launch.

## Validation

The full `scripts/validate_offline.py` route remains fail-closed on namespace or
subprocess inheritance failure. Existing simulator/agent demos and evaluator checks
are preserved. It adds these checks:

- Strict snapshot, arithmetic, cumulative-versus-delta, replay binding and unknown
  versus derived allocation regressions.
- Theme text/accent contrast on both charcoal backgrounds, readable body size,
  scenario-specific simulated-payment labels, shared summary and fresh-run totals.
- Real [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)
  rendering and repeated reruns, invalid-data output, synthetic secret non-disclosure,
  and guards against backend imports, SQLite access and protected file reads.
- A real server launched through the supported entrypoint in a verified network
  namespace. Linux inotify watches nine synthetic config/secrets/.env files across
  original HOME, CWD and script-adjacent roots; any open/access fails the check.
  It verifies loopback-only listening, health/HTML responses and child shutdown.
  This check requires Linux `ip` and inotify, and never changes the host interface.

The health/HTML check is not a browser rendering test. A separate local headless
Chrome check exercises the actual page/WebSocket session, all three tabs and reload
inside a loopback-only OS namespace, repeated switching, evidence expanders and
desktop/narrow viewports. It records page requests and rendered contrast, and captures
screenshots for visual inspection.
It is not a CI browser dependency. Its detailed results and the final candidate
manifest are retained outside the repository for independent review.

No hosted CI result is claimed for this uncommitted viewer. Tests do not establish
general model quality, production access control, real billing or live x402 readiness.
