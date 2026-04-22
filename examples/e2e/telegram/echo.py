"""Telegram E2E — echo scenario.

What this tests
---------------
- Bot receives Telegram ``message`` updates via webhook (verified with
  the ``x-telegram-bot-api-secret-token`` header).
- Direct-messages (``chat.type == "private"``) fire ``on_direct_message``.
- Group mentions (``@<bot-username>``) fire ``on_new_mention``.
- Outbound ``thread.post(...)`` posts a reply via Telegram's
  ``sendMessage`` Bot API.

Required env vars
-----------------
- ``TELEGRAM_BOT_TOKEN``              Bot token from ``@BotFather``.
- ``TELEGRAM_WEBHOOK_SECRET_TOKEN``   Secret token you pass to
                                      ``setWebhook`` — Telegram echoes it
                                      back in the ``x-telegram-bot-api-
                                      secret-token`` header on every
                                      delivery.

Optional
- ``TELEGRAM_BOT_USERNAME``  Bot's ``@username`` (no ``@``). Falls back to
                             the name returned by ``getMe`` during
                             ``initialize``; set this explicitly if you
                             don't want a startup API call.
- ``E2E_PORT``               Webhook server port (default ``8000``).

Run
---
::

    uv sync --group e2e
    uv run python examples/e2e/telegram/echo.py

In a second terminal::

    ngrok http 8000

Then register the webhook (once — or whenever the ngrok URL changes)::

    curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \\
      -d url="https://<your-ngrok>.ngrok.app/api/webhooks/telegram" \\
      -d secret_token="${TELEGRAM_WEBHOOK_SECRET_TOKEN}" \\
      -d allowed_updates='["message","edited_message","callback_query","message_reaction"]'

Telegram setup (once)
---------------------
1. **Create the bot** — message ``@BotFather`` on Telegram → ``/newbot`` →
   pick a display name and ``@username``. Copy the token it returns →
   ``TELEGRAM_BOT_TOKEN``.
2. **Generate a secret token** — any random string 1-256 chars matching
   ``[A-Za-z0-9_-]``. Store it as ``TELEGRAM_WEBHOOK_SECRET_TOKEN``.
3. **Disable privacy mode** (optional, for group tests) — ``@BotFather``
   → ``/setprivacy`` → ``Disable``. Without this the bot only sees
   messages that directly mention it in groups.
4. **Register the webhook** — see the ``curl`` snippet above.
5. **Test** — open a DM with the bot and send any message; the script
   prints the inbound text and replies with ``echo: <text>``. In a
   group, mention the bot: ``@<bot-username> hello``.

Notes
-----
- ``edit_message`` raises :class:`chat.NotImplementedError` in one
  specific edge case (Telegram's ``editMessageText`` returning ``True``
  with no cached message). Upstream has the same stub. See
  ``docs/parity.md``.
- Subscription is implicit — Telegram delivers every message in chats
  the bot is a member of once the webhook is registered;
  ``thread.subscribe()`` is a no-op.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make ``examples/e2e/_common.py`` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import load_env, require_env, run_webhook_server
from chat import Chat
from chat_adapter_state_memory import create_memory_state
from chat_adapter_telegram import create_telegram_adapter

load_env()
require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET_TOKEN")

bot = Chat(
    user_name=os.environ.get("TELEGRAM_BOT_USERNAME", "telegram_bot"),
    adapters={"telegram": create_telegram_adapter()},
    state=create_memory_state(),
)


@bot.on_direct_message
async def on_dm(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] dm from {message.author.user_id}: {message.text!r}")
    await thread.post(f"echo: {message.text}")


@bot.on_new_mention
async def on_mention(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] mention from {message.author.user_id}: {message.text!r}")
    await thread.post(f"hi {message.author.full_name or message.author.user_name}, I'm listening.")


@bot.on_new_message
async def on_any_message(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] message: {message.text!r}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "telegram", port=port)
