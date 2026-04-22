# chat-py

> **Unified chat SDK for Python** — build bots across Slack, Microsoft Teams, Google Chat, Discord, Telegram, GitHub, Linear, and WhatsApp. Write your bot logic once, deploy everywhere. A Python port of [`vercel/chat`](https://github.com/vercel/chat).

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/release/python-3130/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/desplega-ai/chat-py/actions/workflows/ci.yml/badge.svg)](https://github.com/desplega-ai/chat-py/actions/workflows/ci.yml)

---

## Motivation

Vercel's [`chat`](https://github.com/vercel/chat) SDK is, in our view, the cleanest model for writing cross-platform chat bots we've seen: an mdast-based markdown core, platform-agnostic cards/modals/streaming, lockable thread handlers, and a "bot ergonomics first" API. It lands in TypeScript.

A lot of modern agent and backend work — LangGraph, DSPy, CrewAI, FastAPI services, data pipelines, internal tools — lives in Python. Rather than ask Python teams to stand up a TypeScript sidecar, `chat-py` mirrors the Chat SDK's API surface 1:1 so that:

1. **Patterns transfer.** Documentation and examples at [chat-sdk.dev](https://chat-sdk.dev) map directly onto the Python API (function names, card builders, handler signatures, thread/message/channel semantics).
2. **Cross-language consistency.** Teams mixing Python and TypeScript services can use the same bot primitives in both.
3. **Batteries included for the Python ecosystem.** `uv`-native workspace, async everywhere, `pydantic`-compatible data classes, FastAPI / Starlette / aiohttp examples.

`chat-py` is maintained by [Desplega Labs](https://desplega.sh?utm_source=github_chat-py) as an independent port — not affiliated with Vercel. We track upstream closely and contribute fixes back where relevant.

## Installation

A `chat-py` bot needs three pieces: the **core SDK** (`chat-py`), at least one **platform adapter** (`chat-py-adapter-<slack|teams|…>`), and exactly one **state adapter** (`chat-py-adapter-state-<memory|redis|ioredis|pg>`). The state adapter is not optional — it's where thread subscriptions, lock tokens, and dedupe/cache data live. Use `state-memory` for local development and one of the persistent backends in production.

```bash
uv add chat-py
uv add chat-py-adapter-slack chat-py-adapter-state-memory
```

Or with `pip`:

```bash
pip install chat-py chat-py-adapter-slack chat-py-adapter-state-memory
```

## Quickstart

```python
import asyncio
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
    await thread.post("Hello! I'm listening to this thread.")


@bot.on_subscribed_message
async def echo(thread, message):
    await thread.post(f"You said: {message.text}")


# Mount under any ASGI framework — FastAPI shown here
from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/api/webhooks/slack")
async def slack_webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    return await bot.handle_webhook("slack", body, headers)
```

See the [Getting Started guide](docs/getting-started.md) for a full walkthrough and the [`examples/`](examples/) directory for runnable projects.

### Manual end-to-end tests

Per-adapter scripts that boot a FastAPI webhook server and drive the real provider API live under [`examples/e2e/`](examples/e2e/) (one file per scenario). These are **not** pytest tests — they're meant for local smoke / pre-release verification. Each script's docstring lists the env vars it needs; the script exits early with a clear message if any are missing.

```bash
uv sync --group e2e                             # fastapi + uvicorn + python-dotenv
uv run python examples/e2e/slack/echo.py        # @mention echo; set SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET in .env first
```

Full instructions (including how to write new scenarios) in [`examples/e2e/README.md`](examples/e2e/README.md).

## Supported platforms

| Platform          | Package                        | Status   | Mentions | Reactions | Cards | Modals | Streaming | DMs |
| ----------------- | ------------------------------ | -------- | -------- | --------- | ----- | ------ | --------- | --- |
| Slack             | `chat-adapter-slack`           | Shipped  | Yes      | Yes       | Yes   | Yes    | Native    | Yes |
| Microsoft Teams   | `chat-adapter-teams`           | Shipped¹ | Yes      | Read-only | Yes   | No     | Post+Edit | Yes |
| Google Chat       | `chat-adapter-gchat`           | Shipped  | Yes      | Yes       | Yes   | No     | Post+Edit | Yes |
| Discord           | `chat-adapter-discord`         | Shipped  | Yes      | Yes       | Yes   | No     | Post+Edit | Yes |
| GitHub            | `chat-adapter-github`          | Shipped  | Yes      | Yes       | No    | No     | No        | No  |
| Linear            | `chat-adapter-linear`          | Shipped  | Yes      | Yes       | No    | No     | No        | No  |
| Telegram          | `chat-adapter-telegram`        | **Stub**²| —        | —         | —     | —      | —         | —   |
| WhatsApp          | `chat-adapter-whatsapp`        | **Stub**²| —        | —         | —     | —      | —         | —   |

¹ Two known gaps raise at import/call time: the Graph API conversation reader and certificate-based auth. See [`CHANGELOG.md`](CHANGELOG.md).
² v0.1.0 ships placeholder stubs for Telegram (DES-182) and WhatsApp (DES-183) — import is safe but no adapter logic yet. The package surface and README describe the planned feature set.

## Features

- **Event handlers** — mentions, messages, reactions, button clicks, slash commands, modals
- **AI streaming** — stream LLM responses with native Slack streaming and post+edit fallback
- **Cards** — builder API for interactive cards (Slack Block Kit, Teams Adaptive Cards, Google Chat Cards)
- **Actions** — handle button clicks and dropdown selections
- **Modals** — form dialogs with text inputs, dropdowns, and validation
- **Slash commands** — handle `/command` invocations
- **Emoji** — type-safe, cross-platform emoji with custom emoji support
- **File uploads** — send and receive file attachments
- **Direct messages** — initiate DMs programmatically
- **Ephemeral messages** — user-only visible messages with DM fallback

## Packages

| Package                                                    | Description                                                |
| ---------------------------------------------------------- | ---------------------------------------------------------- |
| [`chat`](packages/chat)                                    | Core SDK with `Chat` class, types, card builders, utilities |
| [`chat-adapter-shared`](packages/chat-adapter-shared)      | Shared helpers used by platform adapters                   |
| [`chat-adapter-slack`](packages/chat-adapter-slack)        | Slack adapter                                              |
| [`chat-adapter-teams`](packages/chat-adapter-teams)        | Microsoft Teams adapter                                    |
| [`chat-adapter-gchat`](packages/chat-adapter-gchat)        | Google Chat adapter                                        |
| [`chat-adapter-discord`](packages/chat-adapter-discord)    | Discord adapter                                            |
| [`chat-adapter-telegram`](packages/chat-adapter-telegram)  | Telegram adapter                                           |
| [`chat-adapter-github`](packages/chat-adapter-github)      | GitHub adapter                                             |
| [`chat-adapter-linear`](packages/chat-adapter-linear)      | Linear adapter                                             |
| [`chat-adapter-whatsapp`](packages/chat-adapter-whatsapp)  | WhatsApp (Meta Cloud API) adapter                          |
| [`chat-adapter-state-memory`](packages/chat-adapter-state-memory) | In-memory state adapter (development/testing)       |
| [`chat-adapter-state-redis`](packages/chat-adapter-state-redis)   | Redis state adapter                                 |
| [`chat-adapter-state-ioredis`](packages/chat-adapter-state-ioredis) | Pipeline-oriented Redis state adapter             |
| [`chat-adapter-state-pg`](packages/chat-adapter-state-pg)  | PostgreSQL state adapter                                   |

## Design & API parity

`chat-py` aims for **1:1 behavioral parity** with upstream. Concretely:

- **Same types, Pythonic casing.** `ChatConfig`, `Thread`, `Channel`, `Message` remain capitalized; methods and fields switch to `snake_case` (`thread.post()` identical; `threadId` becomes `thread_id`; `onNewMention` becomes `on_new_mention`).
- **Same mdast AST.** The canonical formatted-content representation is the mdast dict shape used in the TS SDK, so serialized messages are cross-language compatible.
- **Same handler semantics.** `on_new_mention`, `on_new_message`, `on_subscribed_message`, `on_reaction`, `on_action`, `on_modal_submit`, `on_slash_command`, `on_direct_message`.
- **JSX cards → builder functions.** Python has no JSX, so `<Card>...</Card>` becomes `Card(children=[...])`. The exported builder names, props, and card semantics match the TS SDK.
- **Async everywhere.** `asyncio`-native; handlers are `async def`.

Differences from upstream that can't be avoided:

- Python has one first-class Redis client (`redis-py`), so `chat-adapter-state-redis` and `chat-adapter-state-ioredis` both wrap `redis.asyncio` but expose API variants that mirror the TS shape. Users can pick either.
- Microsoft's Teams v2 TypeScript SDK does not have a stable Python equivalent. `chat-adapter-teams` talks to the Bot Framework REST API directly via `httpx` + `msal`.

See [`docs/parity.md`](docs/parity.md) for the full upstream-to-Python mapping.

## Development

```bash
# Clone and sync workspace
git clone https://github.com/desplega-ai/chat-py.git
cd chat-py
uv sync --all-packages --dev

# Run tests
uv run pytest packages/

# Lint + format
uv run ruff check packages/
uv run ruff format packages/

# Type-check the core
uv run mypy packages/chat/src
```

Full contributor guide: [`CONTRIBUTING.md`](CONTRIBUTING.md). Agent-assisted contributions: [`CLAUDE.md`](CLAUDE.md).

## Roadmap

- `v0.1.0` — initial port of `vercel/chat@4.26.0` (this release)
- `v0.2.0` — feature-complete with upstream, CI stable, integration tests green
- `v1.0.0` — pinned to upstream `chat@5.0` with stable API commitment

See the [upstream changelog](https://github.com/vercel/chat/blob/main/packages/chat/CHANGELOG.md) for what lands next.

## License

[MIT](LICENSE) — same as upstream.

## Acknowledgements

`chat-py` is a port of [`vercel/chat`](https://github.com/vercel/chat). Thanks to the Vercel chat team for open-sourcing an API worth porting. All architectural credit belongs upstream; any bugs in the Python translation belong to us.
