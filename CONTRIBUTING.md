# Contributing

Thanks for your interest in improving this sample!

## Ways to help

- **Bug reports** — open an issue with: what you did, what you expected, what happened (logs from `runtime.sh logs <id>` help a lot).
- **Feature ideas** — keep the sample *minimal*: it exists to teach the GreenNode AgentBase platform (Runtime + Memory + MCP Gateway + Policy), not to be a full product.
- **Docs** — clarity fixes to the README / Portal steps are very welcome (English; the bot's replies and the UI text are Vietnamese).

## Pull requests

1. Fork & branch: `feat/my-change`.
2. Backend stays **no-build** Python (SDK `greennode-agentbase`), frontend stays **vanilla** (no framework, no CDN).
3. Use Python 3.12 and run the checks CI runs (no network or credentials needed, the tests use fakes):
   ```bash
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -r src/backend/requirements.txt pytest ruff
   ruff check --select F,E9,B,UP,SIM --target-version py312 src tests
   pytest -q
   node --check src/frontend/app.js
   ```
4. Code comments, docstrings, log messages and docs are English. Only text a user reads in the chat UI or in a bot reply is Vietnamese.
5. Add a test for every behaviour you change, and update `.env.example` and the README when you add an env var or change an endpoint.
6. Keep secrets out: `.env` / `.env.*` are git-ignored (only `.env.example` is tracked) — never commit tokens or API keys.
7. PRs should pass CI (lint, tests, frontend syntax check, `docker build`) before review.
