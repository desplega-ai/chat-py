# chat-adapter-state-redis

Redis state adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/state-redis`](https://github.com/vercel/chat/tree/main/packages/state-redis).

Wraps `redis.asyncio` (`redis-py`'s async client). Suitable for production. Uses `SET NX PX` for locks, server-side TTL for queue entries and cached values.

## Install

```bash
uv add chat-py-adapter-state-redis
```

## Auth / config

| Argument   | Notes                                                                   |
| ---------- | ----------------------------------------------------------------------- |
| `url=`     | Redis connection URL (e.g. `redis://:password@host:6379/0`). Adapter owns the client lifecycle. |
| `client=`  | Pre-built `redis.asyncio.Redis` client. Adapter does not close it.      |
| `key_prefix=` | Optional prefix (default `"chat:"`) for all keys.                    |

Exactly one of `url=` or `client=` must be provided.

## Minimal example

```python
from chat import Chat
from chat_adapter_state_redis import create_redis_state
from chat_adapter_slack import create_slack_adapter

bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},
    state=create_redis_state(url="redis://localhost:6379"),
)
```

## Parity notes

- Python unified `node-redis` and `ioredis` onto `redis-py`'s async client, so both `chat-adapter-state-redis` and `chat-adapter-state-ioredis` wrap the same underlying client. The only on-the-wire difference is the lock token prefix (`redis_` here, `ioredis_` in the ioredis package) — byte-for-byte compatible with the TypeScript counterparts.
- Lua scripts are not required; `SET NX PX` handles atomic lock acquisition.

## Test

```bash
uv run pytest packages/chat-adapter-state-redis

# Live — requires a Redis instance
REDIS_URL=redis://localhost:6379 uv run pytest packages/chat-integration-tests -k redis
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/state-redis
