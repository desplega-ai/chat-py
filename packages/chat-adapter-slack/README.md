# chat-adapter-slack

Slack adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-slack`](https://github.com/vercel/chat/tree/main/packages/adapter-slack).

## Install

```bash
uv add chat-py chat-py-adapter-slack chat-py-adapter-state-memory
```

## Auth / config

`SlackAdapterConfig` — supplied inline or read from the environment.

| Config field              | Env var                                      | Notes                                       |
| ------------------------- | -------------------------------------------- | ------------------------------------------- |
| `botToken`                | `SLACK_BOT_TOKEN`                            | Required. `xoxb-...`                        |
| `signingSecret`           | `SLACK_SIGNING_SECRET`                       | Required for webhook signature verify.      |
| `botUserId`               | `SLACK_BOT_USER_ID`                          | Used to detect self-mentions.               |
| `appToken`                | `SLACK_APP_TOKEN`                            | `xapp-...` — Socket Mode only.              |
| `clientId` / `clientSecret` | `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET`  | OAuth / distribution.                       |
| `encryptionKey`           | `SLACK_ENCRYPTION_KEY`                       | 32-byte base64 for installation tokens.     |
| `mode`                    | —                                            | `"single-workspace"` or `"multi-workspace"`.|

## Minimal example

```python
from chat import Chat
from chat_adapter_slack import create_slack_adapter
from chat_adapter_state_memory import create_memory_state

bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},  # reads SLACK_* env vars
    state=create_memory_state(),
)


@bot.on_new_mention
async def greet(thread, message):
    await thread.post("Hi!")
```

Mount `bot.handle_webhook("slack", body, headers)` under `/api/webhooks/slack` in your ASGI framework.

## Features

- Events API + Socket Mode + OAuth distribution
- Native Block Kit card translation (`card_to_block_kit`)
- Native Slack streaming (`assistant.threads.setStatus` + `chat.update`)
- Modals (`modal_to_slack_view`), slash commands, reactions, file uploads
- Encrypted token helpers for multi-workspace installation storage (`encrypt_token` / `decrypt_token`)
- `parse_slack_message_url` → `SlackThreadId` for deep links

## Parity notes

- Config keys keep upstream's camelCase (`botToken`, `signingSecret`, …) — Pythonic casing is reserved for handler methods, not TypedDict payloads.
- Multi-workspace OAuth flow ports 1:1.
- Markdown ↔ mdast conversion matches upstream's `SlackMarkdownConverter`.
- **Known pre-existing test failures** carried over from the upstream test suite — see [`CHANGELOG.md`](../../CHANGELOG.md).

## Test

```bash
# Unit tests (no network)
uv run pytest packages/chat-adapter-slack

# Live integration tests — require a workspace + bot token
SLACK_TOKEN=xoxb-... uv run pytest packages/chat-integration-tests -k slack
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-slack
