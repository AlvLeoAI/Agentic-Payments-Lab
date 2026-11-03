# Agentic Payments Lab

Phase 1 remains an offline payment simulation. Phase 2 authorizes an OpenAI
Agents SDK / Responses buyer over that simulator. Live OpenAI calls require
separate explicit authorization; offline validation must never need credentials.
Do not enable blockchain, wallet, signing, x402, or facilitator integrations.

- Use Python 3.12, uv, Pydantic, SQLite, FastAPI, HTTPX, and pytest.
- Install the locked environment with `uv sync --locked`.
- Verify with `uv run --offline --no-sync pytest`, `uv run --offline --no-sync ruff check .`,
  and `uv run --offline --no-sync ruff format --check .`.
- Run the credential-free demo with `uv run --offline --no-sync payments-lab demo`.
- Preserve integer atomic amounts, transactional budget reservations, immutable
  purchase identity, and the separation of payment and delivery states.
- Never retry an uncertain payment. Reconcile the existing authorization.
- Keep SDK imports in `buyer_agent/`; deterministic financial code must stay
  vendor-independent. Merchant traffic stays in HTTPX ASGI transport. Only the
  explicitly activated live buyer may use OpenAI Responses, with a named model.
- Bind task, subject, purpose and quality outside model arguments. Preserve narrow
  tools, strict schemas, run limits, disabled tracing, and authoritative reports.
- Run `scripts/validate_offline.py` inside OS network isolation after installing
  dependencies; preserve its fail-closed checks and repeated simulator/agent demos.
- Document simulation limitations; simulated receipts are not blockchain evidence.
- Do not commit secrets or runtime databases. Do not commit or push without user approval.
