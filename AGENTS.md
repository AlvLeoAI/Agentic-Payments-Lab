# Agentic Payments Lab

Phase 1 remains an offline payment simulation. Phase 2 authorizes an OpenAI
Agents SDK / Responses buyer over that simulator. Live OpenAI calls require
separate explicit authorization; offline validation must never need credentials.
Phase 3B.1 permits offline x402 v2 validation and a durable gate exercised only
with injected signer/dispatcher doubles. Phase 3B.2 adds the explicitly enrolled
ASGI-only merchant, durable request/nonce bindings and settlement doubles. Do not
enable live blockchain, wallet, cryptographic signing, RPC, external x402 HTTP
transport, or facilitator integrations.

- Use Python 3.12, uv, Pydantic, SQLite, FastAPI, HTTPX, and pytest.
- Install the locked environment with `uv sync --locked`.
- Verify with `uv run --offline --no-sync pytest`, `uv run --offline --no-sync ruff check .`,
  and `uv run --offline --no-sync ruff format --check .`.
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
