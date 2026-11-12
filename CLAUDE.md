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
- Never open, initialize, migrate or overwrite retained databases. The evidence
  directory named in the handoff is read-only: `sha256sum --check` and reading files
  are fine; never write, delete or regenerate anything there.
- Ask before running project code: pytest, `scripts/validate_offline.py`,
  `payments-lab demo`, the viewer, or `scripts/prepare_runtime.py` (it downloads).
  AGENTS.md says *how* to verify; this rule says *when*. `uv --offline` only blocks
  package downloads. It is not network isolation, so full validation still runs
  under `unshare` as the handoff describes. Read-only `git` commands and
  `sha256sum --check` on the evidence directory need no approval.
- Do not commit or push without user approval.

`.claude/settings.json` denies the obvious live-runner and credential commands. It is
a backstop, not the boundary: the rules above apply even where no pattern matches.
