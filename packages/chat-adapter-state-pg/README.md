# chat-adapter-state-pg

PostgreSQL state adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/state-pg`](https://github.com/vercel/chat/tree/main/packages/state-pg).

Wraps `asyncpg`. Uses `SELECT ... FOR UPDATE SKIP LOCKED` for lock acquisition and TTL-indexed rows for queues and cached values. Suitable for deployments that already have Postgres and want to avoid operating a Redis.

## Install

```bash
uv add chat-py-adapter-state-pg
```

## Auth / config

| Argument   | Notes                                                                   |
| ---------- | ----------------------------------------------------------------------- |
| `url=`     | Postgres DSN (e.g. `postgres://user:pass@host/db`). Adapter creates and owns the pool. |
| `pool=`    | Pre-built `asyncpg.Pool`. Adapter does not close it.                    |
| `client=`  | Pre-built asyncpg connection. Adapter does not close it.                |
| `schema=`  | Optional schema prefix (default `public`).                              |

Tests can also inject any pool-like object that implements `query(text, *params) -> list[Mapping]` and `close()`.

## Minimal example

```python
from chat import Chat
from chat_adapter_state_pg import create_postgres_state
from chat_adapter_slack import create_slack_adapter

bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},
    state=create_postgres_state(url="postgres://localhost/chat"),
)
```

## Schema

The adapter lazily creates four tables on first use:

- `chat_locks` — lock rows keyed by `thread_id`, with `token` and `expires_at` columns.
- `chat_queues` — enqueued messages, TTL-expired via background `DELETE WHERE expires_at < now()`.
- `chat_subscriptions` — thread subscriptions (adapter + thread_id).
- `chat_cache` — typed cache entries with TTL.

Migrations are automatic; you can also run them manually — see `docs/state-pg-schema.sql`.

## Parity notes

- Matches upstream's table layout and column names so Python and TypeScript backends can share a database.
- Lock token format (`pg_<ts>_<hex>`) matches upstream byte-for-byte.

## Test

```bash
uv run pytest packages/chat-adapter-state-pg

# Live — requires a Postgres instance
POSTGRES_URL=postgres://localhost/chat_test uv run pytest packages/chat-integration-tests -k pg
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/state-pg
