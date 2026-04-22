"""Google Chat E2E — echo scenario.

What this tests
---------------
- Bot receives `MESSAGE` events on the HTTP webhook path → replies in the
  same thread.
- (Optional) Bot receives `message.created` Workspace Events via Pub/Sub
  push envelope → routed through the same handler.
- Bearer JWT verification round-trips via `GOOGLE_CHAT_PROJECT_NUMBER` /
  `GOOGLE_CHAT_PUBSUB_AUDIENCE`.

Required env vars
-----------------
- `GOOGLE_CHAT_CREDENTIALS`         Service-account JSON (single line / ``\\n`` escaped)
                                    OR set `GOOGLE_CHAT_USE_ADC=true` if running on GCP.
- `GOOGLE_CHAT_PROJECT_NUMBER`      Chat app's GCP project number (for webhook JWT audience).
- `GCHAT_BOT_NAME`                  Bot's user ID (``users/...``) — used to detect self-mentions.
                                    When unset, the bot treats every leading-space argumentText
                                    as a mention (Google Chat convention).

Optional
- `GOOGLE_CHAT_PUBSUB_AUDIENCE`     Pub/Sub JWT audience (if you're pushing via Pub/Sub).
- `GOOGLE_CHAT_PUBSUB_TOPIC`        ``projects/<p>/topics/<t>`` — when set, the adapter
                                    auto-subscribes to Workspace Events on ADDED_TO_SPACE.
- `GCHAT_APP_URL`                   The public URL the Chat app will call — informational only.
- `E2E_PORT`                        defaults to 8000

Run
---
    uv sync --group e2e
    uv run python examples/e2e/gchat/echo.py

In a second terminal:
    ngrok http 8000
    # paste the https://…ngrok.app URL + `/api/webhooks/gchat` into
    # Google Cloud console → your Chat app → Configuration → App URL

Google Chat app setup (once)
----------------------------
- Create a GCP project (or reuse one); enable the Google Chat API.
- Create a service account with the `Chat Bot` role and download its
  JSON key. Paste the JSON as a single line into `.env` as
  `GOOGLE_CHAT_CREDENTIALS='{...}'`.
- In https://console.cloud.google.com/apis/api/chat.googleapis.com/config
  set the Chat app's App URL to `https://<ngrok>.ngrok.app/api/webhooks/gchat`.
- Add the bot to a space (``@Bot``-mention or invite) and send a message.

For the Pub/Sub flow (optional)
-------------------------------
- Create a Pub/Sub topic + push subscription that POSTs to
  `/api/webhooks/gchat` (the same endpoint — this script auto-detects the
  envelope shape).
- Set `GOOGLE_CHAT_PUBSUB_TOPIC` so ``ADDED_TO_SPACE`` triggers a
  Workspace Events subscription automatically.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `examples/e2e/_common.py` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import load_env, require_env, run_webhook_server  # noqa: E402
from chat import Chat  # noqa: E402
from chat_adapter_gchat import create_google_chat_adapter  # noqa: E402
from chat_adapter_state_memory import create_memory_state  # noqa: E402

load_env()

# Either service account JSON or ADC must be present.
if not os.environ.get("GOOGLE_CHAT_CREDENTIALS") and os.environ.get("GOOGLE_CHAT_USE_ADC") != "true":
    sys.exit(
        "[e2e] need GOOGLE_CHAT_CREDENTIALS=<service-account-json> "
        "or GOOGLE_CHAT_USE_ADC=true in .env"
    )
require_env("GOOGLE_CHAT_PROJECT_NUMBER")

bot = Chat(
    user_name=os.environ.get("GCHAT_BOT_NAME", "chat-py-e2e"),
    adapters={"gchat": create_google_chat_adapter()},  # picks up GOOGLE_CHAT_* env vars
    state=create_memory_state(),
)


@bot.on_new_mention
async def on_mention(thread, message):
    print(f"[e2e] mention from {message.author.user_id}: {message.text!r}")
    await thread.subscribe()
    await thread.post(f"hi <{message.author.user_id}>, I'm subscribed now — reply and I'll echo.")


@bot.on_subscribed_message
async def on_thread_message(thread, message):
    if message.author.is_me:
        return
    print(f"[e2e] subscribed-thread message: {message.text!r}")
    await thread.post(f"echo: {message.text}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "gchat", port=port)
