# Agentic Payments Lab

Phase 1 is an offline simulation. Do not enable OpenAI, blockchain, wallet,
or facilitator integrations without explicit authorization for a later phase.

- Use Python 3.12, uv, Pydantic, SQLite, FastAPI, HTTPX, and pytest.
- Install the locked environment with `uv sync --locked`.
- Verify with `uv run --offline --no-sync pytest`, `uv run --offline --no-sync ruff check .`,
  and `uv run --offline --no-sync ruff format --check .`.
- Run the credential-free demo with `uv run --offline --no-sync payments-lab demo`.
- Preserve integer atomic amounts, transactional budget reservations, immutable
  purchase identity, and the separation of payment and delivery states.
- Never retry an uncertain payment. Reconcile the existing authorization.
- Keep the domain independent of vendor SDKs and keep runtime traffic in the
  HTTPX ASGI transport. Do not introduce network transports in Phase 1.
- Document simulation limitations; simulated receipts are not blockchain evidence.
- Do not commit secrets or runtime databases. Do not commit or push without user approval.
