"""WhatsApp E2E — echo scenario.

What this tests
---------------
- Bot receives ``messages`` webhooks from the WhatsApp Cloud API → replies
  in the same 1:1 DM thread.
- Verify-token GET handshake (``hub.mode=subscribe`` + ``hub.verify_token``)
  returns the ``hub.challenge`` value when you click "Verify" in Meta for
  Developers.
- HMAC-SHA256 signature verification round-trips
  (``X-Hub-Signature-256`` using ``WHATSAPP_WEBHOOK_SECRET`` — the App
  Secret from Meta for Developers → Basic Settings).

WhatsApp is DM-only (no channels), so every inbound message flows through
``on_direct_message``.

Required env vars
-----------------
- ``WHATSAPP_ACCESS_TOKEN``     permanent or temporary access token for the
                                WhatsApp Business app (System User / test
                                token from the API Setup tab).
- ``WHATSAPP_PHONE_NUMBER_ID``  the numeric Phone Number ID of your
                                business test number (not the display
                                phone number). Find it under
                                WhatsApp → API Setup.
- ``WHATSAPP_WEBHOOK_SECRET``   the App Secret (Basic Settings → App
                                Secret). Meta signs each webhook POST
                                using HMAC-SHA256 over the raw body with
                                this secret.
- ``WHATSAPP_VERIFY_TOKEN``     any opaque string you invent; you paste
                                the same value into Webhooks → "Verify
                                token" in the Meta dashboard so the GET
                                handshake succeeds.

Optional
- ``WHATSAPP_BOT_USERNAME``     display name in logs (default
                                ``whatsapp-bot``).
- ``E2E_PORT``                  defaults to 8000.

Run
---
    uv sync --group e2e
    uv run python examples/e2e/whatsapp/echo.py

In a second terminal:
    ngrok http 8000
    # paste the https://…ngrok.app URL + ``/api/webhooks/whatsapp`` into
    # the Meta dashboard → WhatsApp → Configuration → Callback URL, then
    # set "Verify token" to the value of ``WHATSAPP_VERIFY_TOKEN`` and
    # click "Verify and save".

Meta for Developers setup (once)
--------------------------------
- Go to https://developers.facebook.com/apps and create (or open) a
  Business-type app.
- Add the "WhatsApp" product — this provisions a test business phone
  number under WhatsApp → API Setup. Copy the ``Phone number ID`` into
  ``WHATSAPP_PHONE_NUMBER_ID``.
- Under WhatsApp → API Setup, generate a temporary access token (or wire
  up a System User for a permanent one) and copy it into
  ``WHATSAPP_ACCESS_TOKEN``.
- Under "Basic Settings", reveal the App Secret and copy it into
  ``WHATSAPP_WEBHOOK_SECRET``.
- Under WhatsApp → Configuration → Webhook, paste your ngrok URL
  (``https://…ngrok.app/api/webhooks/whatsapp``) as Callback URL and the
  ``WHATSAPP_VERIFY_TOKEN`` value as Verify token, then click "Verify and
  save" — the adapter answers the GET challenge automatically.
- Click "Manage" on the webhook and subscribe to the ``messages`` field.
- From API Setup, add your personal WhatsApp number as a test recipient
  and send "hi" — the bot echoes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Make ``examples/e2e/_common.py`` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import load_env, require_env, run_webhook_server
from chat import Chat
from chat_adapter_state_memory import create_memory_state
from chat_adapter_whatsapp import create_whatsapp_adapter

load_env()
require_env(
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_WEBHOOK_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
)

# ``create_whatsapp_adapter`` reads ``WHATSAPP_APP_SECRET`` from the env;
# re-export ``WHATSAPP_WEBHOOK_SECRET`` → ``WHATSAPP_APP_SECRET`` for
# dotenv users who prefer the more explicit "WEBHOOK_" prefix (matches
# the other adapters' env-var naming).
os.environ.setdefault("WHATSAPP_APP_SECRET", os.environ["WHATSAPP_WEBHOOK_SECRET"])

bot = Chat(
    user_name=os.environ.get("WHATSAPP_BOT_USERNAME", "whatsapp-bot"),
    adapters={"whatsapp": create_whatsapp_adapter()},
    state=create_memory_state(),
)


@bot.on_direct_message
async def on_dm(thread: Any, message: Any, _channel: Any = None) -> None:
    print(f"[e2e] dm from @{message.author.user_name}: {message.text!r}")
    await thread.post(f"echo: {message.text}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "whatsapp", port=port)
