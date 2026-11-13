# Agentic Payments Lab — Claude Code entry point

@AGENTS.md

Current state, evidence and next step: [docs/handoff.md](docs/handoff.md). Read its
"Start here" section before any task.

## Hard rules

- **No live calls.** No OpenAI/Anthropic requests, no `--mode live`, no `run_live_*`
  entry points, no live x402/blockchain/wallet/facilitator traffic. Preparation,
  docs, tests or commit approval never authorize execution; only an explicit
  per-run approval from the user in chat does.
- **No credentials.** Never read, load, source or print `.env` or real API keys.
  Offline work uses synthetic test keys only.
- **No retries without approval.** Never retry, resume, re-sign, reconcile, reset or
  release an uncertain or stopped operation or its exposure unless the user approves
  that specific action.
- Never open, initialize, migrate or overwrite retained databases: any existing
  SQLite file you did not create fresh in the current task (pilot, campaign,
  preflight, authority, buyer or merchant state, wherever it lives). The evidence
  directory named in the handoff is read-only: `sha256sum --check` and reading files
  are fine; never write, delete or regenerate anything there.
- Ask before anything that runs project code or can download. Examples, not a
  complete list: pytest, `uv run ruff`, `uv sync`, `scripts/validate_offline.py`,
  `payments-lab demo`, the viewer, `scripts/prepare_runtime.py`.
  AGENTS.md says *how* to verify; this rule says *when*. `uv --offline` only blocks
  package downloads. It is not network isolation, so full validation still runs
  under `unshare` as the handoff describes. Read-only `git` commands and
  `sha256sum --check` on the evidence directory need no approval.
- **Only the pinned `.runtime/` interpreter.** Never use system `python3` (3.12.3,
  unpatched SQLite 3.45.1) for project code, and never recreate `.venv` (removed
  2026-11-13). A bare `uv run`/`uv sync` falls back to `.venv` and rebuilds it from
  system Python, so always select `UV_PROJECT_ENVIRONMENT=.runtime/venv`
  (`.claude/settings.json` sets it for Claude Code; keep it explicit in commands).
  The only exception is `python3 scripts/prepare_runtime.py`, which installs `.runtime/`.
- Do not commit or push without user approval.

`.claude/settings.json` denies the obvious live-runner and credential commands. It is
a backstop, not the boundary: the rules above apply even where no pattern matches.
