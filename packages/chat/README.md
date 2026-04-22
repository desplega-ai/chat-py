# chat

Core SDK for [`chat-py`](https://github.com/desplega-ai/chat-py) — a Python port of [`vercel/chat`](https://github.com/vercel/chat). Provides the `Chat` class, the `Adapter` / `State` protocols, card builders, markdown / mdast helpers, streaming primitives, and the reviver used across the workspace.

This package is platform-agnostic. You need at least one `chat-adapter-*` and one `chat-adapter-state-*` to run a bot.

## Install

```bash
uv add chat
# or
pip install chat
```

## Minimal example

```python
from chat import Chat
from chat_adapter_slack import create_slack_adapter
from chat_adapter_state_memory import create_memory_state


bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},
    state=create_memory_state(),
)


@bot.on_new_mention
async def greet(thread, message):
    await thread.subscribe()
    await thread.post("Hello.")


@bot.on_subscribed_message
async def echo(thread, message):
    await thread.post(f"You said: {message.text}")
```

Full 5-minute walkthrough: [`docs/getting-started.md`](../../docs/getting-started.md).

## What it ships

- `Chat` — entrypoint; registers handlers, routes webhooks, manages thread locks.
- `Thread`, `Message`, `Channel`, `Plan` — thread-facing primitives.
- `Adapter`, `State` — `Protocol` definitions implemented by every adapter / state package.
- Card builders (`Card`, `Section`, `Button`, `Fields`, `Image`, `Actions`, ...) — Python equivalent of the JSX card DSL in upstream.
- Streaming (`from_full_stream`, `PostableObject`) — AI streaming primitives.
- `serialization.reviver` — reconstructs typed objects from JSON payloads via `_type` discriminators (`chat:Message`, `chat:Thread`, `chat:Channel`, `chat:Plan`).
- `mock_adapter` — `create_mock_adapter`, `create_mock_state`, `create_test_message`, `mock_logger` for test suites.

## Parity notes

- Mirrors `packages/chat` from upstream.
- Methods / fields use `snake_case`; class names stay `CamelCase` (`SerializedChannel` remains `SerializedChannel`).
- mdast nodes stay as `dict[str, Any]` to stay cross-language compatible with remark's output.
- Timestamps are timezone-aware `datetime`, serialized as ISO 8601.
- Error hierarchy matches upstream: `ChatError`, `LockError`, `NotImplementedError`, `RateLimitError`.
- Known gap: 32 `mypy --strict` errors remain in `src/chat` at v0.1.0 — see [`CHANGELOG.md`](../../CHANGELOG.md).

## Test

```bash
uv run pytest packages/chat
uv run pytest packages/chat -k test_thread_post
uv run mypy packages/chat/src   # strict mode — reports the tracked gap
```

## Upstream

- Source: https://github.com/vercel/chat/tree/main/packages/chat
- Docs: https://chat-sdk.dev
