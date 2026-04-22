# chat-adapter-state-ioredis

ioredis-flavoured Redis state adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/state-ioredis`](https://github.com/vercel/chat/tree/main/packages/state-ioredis).

## Why this package exists

In Node.js, upstream ships two separate state packages — `@chat-sdk/state-redis` (wraps the modern `redis` / `node-redis` client) and `@chat-sdk/state-ioredis` (wraps the legacy `ioredis` client). Python has no such split: `redis.asyncio` is the canonical async client. So this package is a **thin subclass of `RedisStateAdapter`** — behaviour is identical; the only difference is the lock-token prefix (`ioredis_` vs `redis_`), which matches upstream byte-for-byte so a single Redis instance can host mixed Python / TypeScript clients that inspect the token prefix.

## Install

```bash
uv add chat-py-adapter-state-ioredis
```

## Minimal example

```python
from chat import Chat
from chat_adapter_state_ioredis import create_ioredis_state
from chat_adapter_slack import create_slack_adapter

bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},
    state=create_ioredis_state(url="redis://localhost:6379"),
)
```

All other config (client injection, key prefix) is inherited from `chat-adapter-state-redis` — see that package's README.

## Parity notes

- See `chat-adapter-state-redis` for the full implementation. The only override is `acquire_lock`, which emits `ioredis_<ts>_<hex>` tokens instead of `redis_<ts>_<hex>`.

## Test

```bash
uv run pytest packages/chat-adapter-state-ioredis

# Live — shares REDIS_URL with state-redis
REDIS_URL=redis://localhost:6379 uv run pytest packages/chat-integration-tests -k ioredis
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/state-ioredis
