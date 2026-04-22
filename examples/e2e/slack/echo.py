"""Slack E2E — echo scenario.

What this tests
---------------
- Bot receives `app_mention` events → replies in-thread.
- Bot receives `message.channels` events in subscribed threads → echoes them.
- Slack request-signature verification round-trips (`SLACK_SIGNING_SECRET`).

Required env vars
-----------------
- `SLACK_BOT_TOKEN`      `xoxb-...`   (OAuth & Permissions page)
- `SLACK_SIGNING_SECRET`              (Basic Information → Signing Secret)

Optional
- `E2E_PORT`             defaults to 8000

Run
---
    uv sync --group e2e
    uv run python examples/e2e/slack/echo.py

In a second terminal:
    ngrok http 8000
    # paste the https://…ngrok.app URL + `/api/webhooks/slack` into
    # Slack app → Event Subscriptions → Request URL

Slack app setup (once)
----------------------
- Create an app at https://api.slack.com/apps
- OAuth scopes (Bot Token): `app_mentions:read`, `chat:write`
- Subscribe to bot events: `app_mention`, `message.channels`
- Install the app into your workspace, copy the bot + signing secret to `.env`
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `examples/e2e/_common.py` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import load_env, require_env, run_webhook_server
from chat import Chat
from chat_adapter_slack import create_slack_adapter
from chat_adapter_state_memory import create_memory_state

load_env()
require_env("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")

bot = Chat(
    user_name="chat-py-e2e",
    adapters={"slack": create_slack_adapter()},  # picks up SLACK_* env vars
    state=create_memory_state(),
)


@bot.on_new_mention
async def on_mention(thread, message, context=None):
    print(f"[e2e] mention from {message.author.user_id}: {message.text!r}")
    await thread.subscribe()
    await thread.post(f"hi <@{message.author.user_id}>, I'm subscribed now — reply and I'll echo.")


@bot.on_subscribed_message
async def on_thread_message(thread, message, context=None):
    print(f"[e2e] subscribed-thread message: {message.text!r}")
    await thread.post(f"echo: {message.text}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "slack", port=port)
