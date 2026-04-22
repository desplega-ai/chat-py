# chat-adapter-whatsapp

WhatsApp (Meta Cloud API) adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-whatsapp`](https://github.com/vercel/chat/tree/main/packages/adapter-whatsapp).

## Install

```bash
uv add chat-py chat-py-adapter-whatsapp chat-py-adapter-state-memory
```

Along with the core SDK and any `chat-py-adapter-state-*` (use `chat-py-adapter-state-redis` / `-state-pg` in production).

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | yes | System User access token for the Cloud API |
| `WHATSAPP_APP_SECRET` | yes | Meta App Secret for `X-Hub-Signature-256` HMAC verification |
| `WHATSAPP_PHONE_NUMBER_ID` | yes | Business phone number ID (not the phone number itself) |
| `WHATSAPP_VERIFY_TOKEN` | yes | Token returned during the `hub.challenge` handshake |
| `WHATSAPP_BOT_USERNAME` | no | Display name for the bot (defaults to `whatsapp-bot`) |
| `WHATSAPP_API_URL` | no | Override the Meta Graph API base URL (defaults to `https://graph.facebook.com`) |

## Minimal example

```python
import asyncio

from chat import Chat
from chat_adapter_state_memory import MemoryState
from chat_adapter_whatsapp import create_whatsapp_adapter


async def main() -> None:
    chat = Chat(
        user_name="my-bot",
        adapters={"whatsapp": create_whatsapp_adapter()},
        state=MemoryState(),
    )

    @chat.on_new_mention
    async def _on_mention(event):  # pragma: no cover - example only
        await event.thread.post(f"Hi {event.message.author.user_name}!")

    # Plug `chat.handle_webhook("whatsapp", body, headers)` into your HTTP
    # framework of choice (FastAPI, Starlette, aiohttp, ...).


asyncio.run(main())
```

## Webhook handling

`WhatsAppAdapter.handle_webhook` answers both:

1. **`GET`** — Meta verification challenge. The adapter checks
   `hub.mode == "subscribe"` and `hub.verify_token` against the configured
   verify token, then echoes `hub.challenge` back with status 200. Pass the
   request URL via the `url=` keyword so the adapter can read the query
   string.
2. **`POST`** — event delivery. The raw body is verified with
   HMAC-SHA256 against the App Secret using
   :func:`hmac.compare_digest`. Mismatched signatures produce `401`.

Each method returns `(status, headers, body)` matching the rest of the
chat-py adapter family.

## Cards & interactive messages

Cards with reply-button `actions` (1-3 buttons) render as an interactive
button payload. Anything else falls back to a WhatsApp-flavoured markdown
message (`*bold*`, `_italic_`, `~strike~`, ```` ```code``` ````). Reply
button IDs encode the action via `chat:{json}` so callbacks round-trip
through `decode_whatsapp_callback_data`.

## Limitations / parity notes

- **No edit / delete.** WhatsApp Cloud API does not support
  `editMessage` / `deleteMessage`; both raise `chat.NotImplementedError`,
  matching upstream's `Error` throws.
- **No history fetch.** `fetch_messages` always returns `{"messages": []}`
  — the Cloud API has no message history endpoint.
- **No typing indicator.** `start_typing` is a no-op.
- **Single-DM threads.** Every conversation is 1:1, so the channel ID is
  the thread ID.

## Test

```bash
uv run pytest packages/chat-adapter-whatsapp
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-whatsapp
