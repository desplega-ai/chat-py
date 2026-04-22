# Contributing to chat-py

Thanks for your interest. This repo is a Python port of [`vercel/chat`](https://github.com/vercel/chat) maintained by [Desplega Labs](https://desplega.ai/labs). We keep the Python API as close to upstream as Python allows.

## Ground rules

1. **Parity first.** If upstream has feature `X`, the default answer is "port it 1:1." Deviate only when Python idioms demand it, and document the deviation in `docs/parity.md`.
2. **Upstream sync discipline.** When upstream changes, we mirror the change in Python in the same release cycle. Reference upstream PRs in your commit messages: `Co-upstream: vercel/chat#1234`.
3. **Test parity.** Every TS unit test has a Python counterpart. If you add functionality, add tests in both `packages/<pkg>/src/*_test.py` or `packages/<pkg>/tests/`.
4. **Typed.** Core (`chat`) is `mypy --strict`. Adapters are strongly typed where the underlying SDK allows.

## Development setup

Prerequisites:

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/) 0.11 or later
- `git`

```bash
git clone https://github.com/desplega-ai/chat-py.git
cd chat-py
uv python install 3.13
uv sync --all-packages --dev
```

`uv sync --all-packages` installs all workspace members in editable mode into a single virtualenv (`.venv/`). You don't need to `pip install -e` each package.

## Common commands

| Command                                         | What it does                                          |
| ----------------------------------------------- | ----------------------------------------------------- |
| `uv run pytest packages/`                       | Run the full test suite                               |
| `uv run pytest packages/chat -k test_thread`    | Run a single package's tests with a filter            |
| `uv run ruff check packages/`                   | Lint                                                  |
| `uv run ruff format packages/`                  | Format                                                |
| `uv run ruff check --fix packages/`             | Autofix lint issues                                   |
| `uv run mypy packages/chat/src`                 | Type-check the core                                   |
| `./scripts/check.sh`                            | Lint + format check (what CI runs)                    |
| `./scripts/test.sh`                             | Run tests with the default config                     |

## Workspace layout

```
packages/
  chat/                         # core SDK — mirrors upstream packages/chat
  chat-adapter-shared/          # shared adapter helpers — packages/adapter-shared
  chat-adapter-slack/           # packages/adapter-slack
  chat-adapter-discord/         # packages/adapter-discord
  chat-adapter-teams/           # packages/adapter-teams
  chat-adapter-gchat/           # packages/adapter-gchat
  chat-adapter-telegram/        # packages/adapter-telegram
  chat-adapter-github/          # packages/adapter-github
  chat-adapter-linear/          # packages/adapter-linear
  chat-adapter-whatsapp/        # packages/adapter-whatsapp
  chat-adapter-state-memory/    # packages/state-memory
  chat-adapter-state-redis/     # packages/state-redis
  chat-adapter-state-ioredis/   # packages/state-ioredis
  chat-adapter-state-pg/        # packages/state-pg
  chat-integration-tests/       # packages/integration-tests
examples/                       # runnable sample projects
docs/                           # markdown docs — parity with apps/docs/content/docs
scripts/                        # dev helpers (check/format/test/typecheck)
```

Each package has its own `pyproject.toml`, `src/<module>/`, and `tests/`. The top-level `pyproject.toml` declares the `uv` workspace and shared dev dependencies.

### Per-package test isolation

Unit tests for each package live under `packages/<pkg>/tests/` (plus inline `_test.py` files under `src/`). Run a single package's suite in isolation:

```bash
uv run pytest packages/chat-adapter-slack
uv run pytest packages/chat-adapter-state-pg
```

This matters during development because some suites load heavy optional deps (`asyncpg`, `redis.asyncio`, `msal`, …) only when imported. Filtering to a single package avoids unrelated import-time surprises.

### Integration test env gating

Integration tests against live services (Slack, Teams, Redis, Postgres, …) are **opt-in** and guarded by environment variables. A missing value produces a `pytest.skip`, so the default `uv run pytest packages/` on a laptop stays green.

| Backend      | Env var             |
| ------------ | ------------------- |
| Slack        | `SLACK_TOKEN`       |
| Teams        | `TEAMS_APP_ID`      |
| Google Chat  | `GCHAT_PROJECT`     |
| Discord      | `DISCORD_TOKEN`     |
| GitHub       | `GITHUB_TOKEN`      |
| Linear       | `LINEAR_API_KEY`    |
| Telegram     | `TELEGRAM_TOKEN`    |
| WhatsApp     | `WHATSAPP_TOKEN`    |
| Redis        | `REDIS_URL`         |
| ioredis      | `REDIS_URL`         |
| Postgres     | `POSTGRES_URL`      |

Helpers live in `chat_integration_tests._env` — `require_backend("redis")` returns the URL or skips the current test. New integration tests should use these helpers rather than reading `os.environ` directly.

## Commit style

- One logical change per commit.
- Subject: `[DES-XXX] <package>: <imperative summary>` (e.g. `[DES-177] chat: port streaming markdown renderer`).
- Body: explain the *why* and link upstream references if ported: `Ports vercel/chat@abc1234`.
- Sign with `Co-Authored-By: Claude <noreply@anthropic.com>` when AI-assisted.

### Linear issue tracking

Every commit must reference the Linear issue ID it belongs to using the `[DES-XXX]` prefix in the subject line. This is how we keep the Linear project board in sync with `main` without PRs.

- Find the ticket for the package/phase you are working on in the [chat-sdk port project](https://linear.app/desplega-labs/project/chat-sdk-port-112924072c77/overview).
- If the work spans multiple tickets, pick the primary one for the subject and mention the others in the body (`Also: DES-YYY, DES-ZZZ`).
- If no ticket exists yet, create one in the project before committing.

## Pull requests / direct pushes

During the initial port (v0.x), maintainers push directly to `main` to keep velocity high. Once we hit `v0.2.0`, all changes go through PRs with review.

## Releases

Releases are managed via [Changesets](https://github.com/changesets/changesets)-equivalent manual version bumps: edit each changed package's `pyproject.toml`, then add a section to `CHANGELOG.md`.

`uv publish` is configured in the CI release workflow (see `.github/workflows/release.yml`).

## Adding a new adapter

1. Create `packages/chat-adapter-<name>/` following the existing layout.
2. Mirror the upstream adapter's public API (adapter constructor, webhook handler, thread ID format, markdown converter).
3. Add integration tests to `packages/chat-integration-tests/tests/test_<name>.py`.
4. Document in `docs/adapters/<name>.md`.
5. Add the adapter to the platform-support matrix in `README.md`.

## Questions

Open an issue on [GitHub](https://github.com/desplega-ai/chat-py/issues) or reach us in our community chat.
