# Agentic Payments Lab

Phase 1 remains an offline payment simulation. Phase 2 authorizes an OpenAI
Agents SDK / Responses buyer over that simulator. Live OpenAI calls require
separate explicit authorization; offline validation must never need credentials.
Phase 3B.1 permits offline x402 v2 validation and a durable gate exercised only
with injected signer/dispatcher doubles. Phase 3B.2 adds the explicitly enrolled
ASGI-only merchant, durable request/nonce bindings and settlement doubles. Do not
enable live blockchain, wallet, cryptographic signing, RPC, external x402 HTTP
transport, or facilitator integrations.
Phase 3B.3 permits bounded synthetic evidence validation and explicit operator
reconciliation. Its fixture source has no network transport and proves no real
chain facts. Keep evidence/accounting/audit atomic in the existing buyer file;
inconclusive observations retain exposure, and finality incidents block new
signing claims/dispatch without rolling back accepted accounting. Buyer version 3
rejects older binaries on reopen; stop old processes before upgrade. No automatic polling,
startup reconciliation, merchant repair or agent/CLI backend switch is authorized.
Phase 3B.4 adds explicit offline buyer delivery acceptance through one non-charging
ASGI retrieval per invocation. Require prior buyer fixture settlement and exact
purchase/request/content binding; acceptance and audit are one append-only buyer
event. Incidents fence new acceptance; accepted history cannot be downgraded.
Current buyer version 4 rejects older binaries on reopen. Keep signatures and
merchant response bodies transient, and preserve settled accounting on every
delivery failure. No automatic retrieval, reconciliation, cache repair or repayment.
Phase 3B.5 connects the bounded buyer to these fixtures through explicit trusted
host Python configuration and the scripted SDK model only. The simulator and CLI
remain unchanged defaults. Preserve the four tool input schemas, immutable buyer
goal/profile/request witness, task-bound quote handles and single-attempt gate.
Model mode is distinct from payment backend; fixture reports never contain a
simulator receipt or claim blockchain settlement. Agent status, continuation and
cleanup cannot reconcile, retrieve/accept delivery, repair or retry payment.
Live model activation on the offline x402 buyer is blocked.
Phase 3B.6 permits the offline evaluator and a separate simulator Responses-mock
suite. Call-budget batches require explicit fresh initialization; missing history,
incomplete invocations, unknown usage and unverified response model/tier fail closed.
Keep the entire serialized request bounded before mock dispatch. Planning cost
reservations are not billing caps or exact token bounds. No live transport, automatic
resume, budget reset, UI or deployment is authorized by this slice.
Phase 3B.7 prepares a simulator-only pilot with a sourced immutable Luna rate card
and documented cache-write lifetimes, still through in-memory Responses mocks.
Use the pinned Linux x86_64 runtime under `.runtime/`; its actual Python executable
and embedded SQLite fix provenance must pass `require_pilot_runtime()`. The old
`.venv`/system SQLite remains unpatched. `initialize_pilot` checks the runtime before
creating fresh state; lower-level host objects and legacy APIs are not a global
runtime sandbox. Never open retained state through initialization, enable live
transport, load credentials or treat a planning ceiling as spending authorization.

- Use Python 3.12, uv, Pydantic, SQLite, FastAPI, HTTPX, and pytest.
- Install the isolated runtime with `python3 scripts/prepare_runtime.py`, then use
  `UV_PROJECT_ENVIRONMENT=.runtime/venv uv sync --locked --no-python-downloads
  --python .runtime/cpython-3.12.16-20261026/bin/python3.12`.
- Verify with `uv run --offline --no-sync pytest`, `uv run --offline --no-sync ruff check .`,
  and `uv run --offline --no-sync ruff format --check .`, selecting
  `UV_PROJECT_ENVIRONMENT=.runtime/venv`. Full runtime validation stays OS-isolated.
- Run the credential-free demo with `uv run --offline --no-sync payments-lab demo`.
- Preserve integer atomic amounts, transactional budget reservations, immutable
  purchase identity, and the separation of payment and delivery states.
- Never retry an uncertain payment. Reconcile the existing authorization.
- Keep OpenAI SDK imports in `buyer_agent/` and x402 SDK imports in `x402_adapter/`;
  deterministic financial code must stay
  vendor-independent. Merchant traffic stays in HTTPX ASGI transport. Only the
  explicitly activated live buyer may use OpenAI Responses, with a named model.
- Bind task, subject, purpose and quality outside model arguments. Preserve narrow
  tools, strict schemas, run limits, disabled tracing, and authoritative reports.
- Run `scripts/validate_offline.py` inside OS network isolation after installing
  dependencies; preserve its fail-closed checks and repeated simulator/agent demos.
- Document simulation limitations; simulated receipts are not blockchain evidence.
- Use fresh, explicitly initialized offline x402 fixtures, never retained smoke
  state. Preserve transient-only signatures, durable single-attempt ownership,
  dispatch fencing and reservations until conclusive evidence. No automatic
  re-signing, resend, recovery or backend switching. Offline evidence is not
  blockchain settlement. Resolve the SQLite WAL-reset patch blocker before live use.
- Preserve merchant settlement claims and identity/nonce tombstones across restart
  and cache expiry. Never retry settlement or reconstruct lost signatures. Merchant
  observations/cached delivery cannot settle buyer accounting. Retrieval and explicit
  deterministic cache recovery are non-charging and stay inside trusted ASGI/host code.
- Do not commit secrets or runtime databases. Do not commit or push without user approval.
