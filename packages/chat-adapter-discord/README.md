# chat-adapter-discord

Discord adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-discord`](https://github.com/vercel/chat/tree/main/packages/adapter-discord).

Targets Discord's HTTP Interactions path (signed webhooks) with REST fall-outs for post / edit / delete / reactions / typing.

## Install

```bash
uv add chat-py chat-py-adapter-discord chat-py-adapter-state-memory
```

## Auth / config

| Config field    | Env var               | Notes                                               |
| --------------- | --------------------- | --------------------------------------------------- |
| `botToken`      | `DISCORD_BOT_TOKEN`   | Required. Bot token from the Discord developer portal. |
| `publicKey`     | `DISCORD_PUBLIC_KEY`  | Required for signature verification.                |
| `applicationId` | `DISCORD_APPLICATION_ID` | Required for slash-command + interaction routing. |
| `mentionRoleIds` | —                    | Optional; role IDs that should be treated as mentions. |

## Minimal example

```python
from chat import Chat
from chat_adapter_discord import create_discord_adapter
from chat_adapter_state_memory import create_memory_state

bot = Chat(
    user_name="mybot",
    adapters={"discord": create_discord_adapter()},  # reads DISCORD_* env vars
    state=create_memory_state(),
)


@bot.on_slash_command("hello")
async def hello(thread, command):
    await thread.post(f"Hello {command.user_name}!")
```

Mount `bot.handle_webhook("discord", body, headers)` under `/api/webhooks/discord`. Incoming `PING` interactions are acknowledged automatically; `MessageComponent` and `ApplicationCommand` interactions dispatch into the chat handler chain.

## Thread ID

Discord thread IDs: `discord:{channelId}:{threadId or messageId}`. `is_dm(thread_id)` short-circuits DM detection.

## Features

- Ed25519 signature verification (`verify_discord_signature`) via PyNaCl
- REST post / edit / delete / reactions / typing (`httpx` async client)
- Button / link / select-menu card translation (`card_to_discord_payload`)
- Fallback text rendering (`card_to_fallback_text`) for components that exceed Discord's limits
- Slash command context (`DiscordSlashCommandContext`, `parse_slash_command`) including deferred-response tracking

## Parity notes

- Button style constants match upstream (`BUTTON_STYLE_PRIMARY` = 1, etc.).
- `DISCORD_MAX_CONTENT_LENGTH` mirrors upstream's 2000-char limit.
- Markdown ↔ mdast conversion matches `DiscordFormatConverter`.

## Test

```bash
uv run pytest packages/chat-adapter-discord

# Live
DISCORD_TOKEN=... uv run pytest packages/chat-integration-tests -k discord
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-discord
