# chat-integration-tests

End-to-end integration tests for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port / counterpart of upstream [`packages/integration-tests`](https://github.com/vercel/chat/tree/main/packages/integration-tests).

Covers:

- **Round-trip dispatch** — per-state-backend (memory, Redis, ioredis, Postgres) — assert webhook → handler → reply flows.
- **Serialization round-trip** — exercise the reviver against real fixtures.
- **Webhook error paths** — malformed bodies, bad signatures, timeouts.

Tests that require live services (Slack, Teams, Redis, …) are **opt-in** and guarded by env vars.

## Install

```bash
uv add --dev chat-integration-tests
```

## Env gating

Every backend reads its own environment variable. A missing value produces a `pytest.skip`, so the default `uv run pytest` on a laptop stays green.

| Backend      | Env var             |
| ------------ | ------------------- |
| Slack        | `SLACK_TOKEN`       |
| Teams        | `TEAMS_APP_ID`      |
| Google Chat  | `GCHAT_PROJECT`     |
| Discord      | `DISCORD_TOKEN`     |
| GitHub       | `GITHUB_TOKEN`      |
| Linear       | `LINEAR_API_KEY`    |
| WhatsApp     | `WHATSAPP_TOKEN`    |
| Telegram     | `TELEGRAM_TOKEN`    |
| Redis        | `REDIS_URL`         |
| ioredis      | `REDIS_URL`         |
| Postgres     | `POSTGRES_URL`      |

Helpers live in `chat_integration_tests._env`:

```python
from chat_integration_tests._env import require_backend

async def test_dispatch_redis():
    url = require_backend("redis")   # skips if REDIS_URL not set
    ...
```

## Run

```bash
# Unit slice (all fake backends)
uv run pytest packages/chat-integration-tests

# Filter to a single backend
uv run pytest packages/chat-integration-tests -k memory
uv run pytest packages/chat-integration-tests -k pg

# Live — set the env var, then run
REDIS_URL=redis://localhost:6379 uv run pytest packages/chat-integration-tests -k redis
POSTGRES_URL=postgres://localhost/chat_test uv run pytest packages/chat-integration-tests -k pg
```

## Parity notes

- Mirrors upstream's integration-test layout where possible; per-backend dispatch tests match `test_dispatch_*.ts` in upstream.
- Webhook error tests exercise the same fixtures as upstream so behaviour stays aligned across languages.

## Upstream

https://github.com/vercel/chat/tree/main/packages/integration-tests
