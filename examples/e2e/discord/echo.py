"""Discord E2E — echo scenario.

What this tests
---------------
- Bot receives `APPLICATION_COMMAND` interactions (slash commands) → replies.
- Bot receives forwarded Gateway `MESSAGE_CREATE` events (bot mention or DM)
  → echoes them.
- Discord request-signature verification round-trips (Ed25519 via
  ``DISCORD_PUBLIC_KEY``).

Required env vars
-----------------
- ``DISCORD_BOT_TOKEN``          bot token (Developer Portal → Bot → Reset Token)
- ``DISCORD_PUBLIC_KEY``         64-hex-char public key (Developer Portal →
                                 General Information → Public Key)
- ``DISCORD_APPLICATION_ID``     application (client) ID

Optional
- ``E2E_PORT``                   defaults to 8000

Run
---
    uv sync --group e2e
    uv run python examples/e2e/discord/echo.py

In a second terminal:
    ngrok http 8000
    # paste the https://…ngrok.app URL + ``/api/webhooks/discord`` into
    # Discord app → General Information → Interactions Endpoint URL.

Discord app setup (once)
------------------------
- Create an application at https://discord.com/developers/applications
- General Information → copy Application ID and Public Key
- Bot → create bot, Reset Token, copy token; enable ``Message Content``
  Gateway Intent if you want to echo plain messages.
- OAuth2 → URL Generator: pick scope ``applications.commands`` (plus ``bot``
  if you also want the bot presence); install the generated URL in your
  server.
- Register at least one slash command (``/echo``) via the Discord REST API,
  for example:
  ``curl -X POST \\
    -H "Authorization: Bot $DISCORD_BOT_TOKEN" \\
    -H "Content-Type: application/json" \\
    -d '{"name":"echo","description":"Echo back what you typed",\\
         "options":[{"name":"text","type":3,"description":"text","required":true}]}' \\
    https://discord.com/api/v10/applications/$DISCORD_APPLICATION_ID/commands``
- Point Discord at your ngrok URL: General Information → Interactions
  Endpoint URL. Discord will send a PING — the adapter auto-replies PONG.
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
from chat_adapter_discord import create_discord_adapter
from chat_adapter_state_memory import create_memory_state

load_env()
require_env("DISCORD_BOT_TOKEN", "DISCORD_PUBLIC_KEY", "DISCORD_APPLICATION_ID")

bot = Chat(
    user_name="chat-py-e2e",
    adapters={"discord": create_discord_adapter()},  # picks up DISCORD_* env vars
    state=create_memory_state(),
)


@bot.on_slash_command("/echo")
async def on_echo(event: dict[str, Any]) -> None:
    channel = event["channel"]
    text = event.get("text") or ""
    print(f"[e2e] /echo from {event['user'].user_name}: {text!r}")
    await channel.post(f"echo: {text}")


@bot.on_new_mention
async def on_mention(thread: Any, message: Any) -> None:
    print(f"[e2e] mention from {message.author.user_id}: {message.text!r}")
    await thread.subscribe()
    await thread.post(
        f"hi <@{message.author.user_id}>, I'm subscribed now — reply and I'll echo."
    )


@bot.on_subscribed_message
async def on_thread_message(thread: Any, message: Any) -> None:
    print(f"[e2e] subscribed-thread message: {message.text!r}")
    await thread.post(f"echo: {message.text}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "discord", port=port)
