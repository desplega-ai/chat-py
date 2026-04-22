"""Slack E2E — echo scenario.

What this tests
---------------
- Bot receives `app_mention` events → replies in-thread.
- Bot receives `message.channels` events in subscribed threads → echoes them.
- Slack request-signature verification round-trips (`SLACK_SIGNING_SECRET`)
  OR Socket Mode websocket delivery (`SLACK_APP_TOKEN`).

Required env vars
-----------------
- `SLACK_BOT_TOKEN`      `xoxb-...`   (OAuth & Permissions page)

Plus one of:
- `SLACK_SIGNING_SECRET`              (webhook mode — default)
- `SLACK_APP_TOKEN`      `xapp-...`   (socket mode — pass `--mode socket`)

Optional
- `E2E_PORT`             defaults to 8000 (webhook mode only)

Run — webhook mode (default)
----------------------------
    uv sync --group e2e
    uv run python examples/e2e/slack/echo.py

In a second terminal:
    ngrok http 8000
    # paste the https://…ngrok.app URL + `/api/webhooks/slack` into
    # Slack app → Event Subscriptions → Request URL

Run — socket mode (no ngrok needed)
-----------------------------------
    uv run python examples/e2e/slack/echo.py --mode socket
    # Slack app → Socket Mode → enable; generate an app-level token with
    # `connections:write` and put it in `.env` as SLACK_APP_TOKEN

Slack app setup (once)
----------------------
- Create an app at https://api.slack.com/apps
- OAuth scopes (Bot Token): `app_mentions:read`, `chat:write`
- Subscribe to bot events: `app_mention`, `message.channels`
- Install the app into your workspace, copy the bot + signing secret to `.env`
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make `examples/e2e/_common.py` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import load_env, require_env, run_socket_client, run_webhook_server
from chat import Chat
from chat_adapter_slack import create_slack_adapter
from chat_adapter_state_memory import create_memory_state

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument(
    "--mode",
    choices=("webhook", "socket"),
    default=os.environ.get("SLACK_MODE", "webhook"),
    help="Delivery mode. webhook (default) or socket. Overrides $SLACK_MODE.",
)
args = parser.parse_args()

load_env()
if args.mode == "socket":
    require_env("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")
else:
    require_env("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")

slack_config: dict[str, object] = {"mode": args.mode}
if bot_token := os.environ.get("SLACK_BOT_TOKEN"):
    slack_config["botToken"] = bot_token

bot = Chat(
    user_name="chat-py-e2e",
    adapters={"slack": create_slack_adapter(slack_config)},  # type: ignore[arg-type]
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
    if args.mode == "socket":
        run_socket_client(bot, "slack")
    else:
        port = int(os.environ.get("E2E_PORT", "8000"))
        run_webhook_server(bot, "slack", port=port)
