# chat-adapter-state-memory

In-memory state adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/state-memory`](https://github.com/vercel/chat/tree/main/packages/state-memory).

Implements the full `State` protocol — locks, subscriptions, queues, and cached values — backed by plain Python dicts. **Single-process only**; intended for development and tests. Use `chat-adapter-state-redis`, `chat-adapter-state-ioredis`, or `chat-adapter-state-pg` in production.

## Install

```bash
uv add chat-adapter-state-memory
```

## Minimal example

```python
from chat import Chat
from chat_adapter_state_memory import create_memory_state
from chat_adapter_slack import create_slack_adapter

bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},
    state=create_memory_state(),
)
```

## Parity notes

- Lock tokens use `secrets.token_hex` for crypto strength (matches upstream's approach).
- TTL enforcement uses integer milliseconds since epoch (matches upstream's `Date.now()`).
- `_LockDict` / `_QueueEntryDict` are redeclared locally as `TypedDict` so the module loads cleanly even before chat-core ships the canonical Protocols.

## Test

```bash
uv run pytest packages/chat-adapter-state-memory
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/state-memory
