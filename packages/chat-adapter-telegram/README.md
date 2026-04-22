# chat-adapter-telegram

Telegram adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-telegram`](https://github.com/vercel/chat/tree/main/packages/adapter-telegram).

## Status

**v0.1.0 — placeholder stub.** The Telegram port is tracked under Linear issue DES-182 and is not yet implemented; importing `chat_adapter_telegram` currently only exposes `__version__`. The top-level support matrix lists Telegram as a target platform; concrete support (bot token auth, webhook verification, markdown ↔ mdast, inline keyboards) lands in a follow-up release.

## Install

```bash
uv add chat-py-adapter-telegram
```

## Planned features

- Bot API token auth (`TELEGRAM_BOT_TOKEN`)
- Webhook signature verification via the secret token header (`X-Telegram-Bot-Api-Secret-Token`)
- Inline keyboards and callback query routing (partial card support)
- MarkdownV2 ↔ mdast converter
- Typing indicators and reactions

## Test

```bash
# The smoke test asserts only that the placeholder imports cleanly.
uv run pytest packages/chat-adapter-telegram
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-telegram
