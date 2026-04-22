"""GitHub E2E — echo scenario.

What this tests
---------------
- Bot receives ``issue_comment.created`` webhooks when a comment on an
  issue or PR mentions ``@<GITHUB_BOT_USERNAME>`` → replies in the same
  thread.
- Once mentioned, the thread is subscribed and subsequent comments trigger
  an ``on_subscribed_message`` echo.
- HMAC-SHA256 signature verification round-trips (``X-Hub-Signature-256``
  using ``GITHUB_WEBHOOK_SECRET``).

Required env vars
-----------------
- ``GITHUB_APP_ID``          numeric App ID (Developer Settings → GitHub Apps)
- ``GITHUB_APP_PRIVATE_KEY`` PEM content of the App's private key (``.pem``
                             file downloaded from the App settings; dotenv
                             users: replace newlines with ``\\n``)
- ``GITHUB_WEBHOOK_SECRET``  matches the App's Webhook Secret field
- ``GITHUB_BOT_USERNAME``    the ``@login`` the bot answers to
                             (e.g. ``my-bot[bot]``)

Optional
- ``GITHUB_INSTALLATION_ID`` pin to a single install (single-tenant mode);
                             omit for multi-tenant mode where the install
                             ID is extracted from each webhook payload.
- ``E2E_PORT``               defaults to 8000

Run
---
    uv sync --group e2e
    uv run python examples/e2e/github/echo.py

In a second terminal:
    ngrok http 8000
    # paste the https://…ngrok.app URL + ``/api/webhooks/github`` into the
    # GitHub App's Webhook URL field.

GitHub App setup (once)
-----------------------
- Register a GitHub App at https://github.com/settings/apps/new
    - Set "Webhook URL" to ``https://…ngrok.app/api/webhooks/github``.
    - Set "Webhook secret" to the value of ``GITHUB_WEBHOOK_SECRET``.
    - Permissions:
        * Pull requests: Read & Write
        * Issues: Read & Write
        * Metadata: Read-only
    - Subscribe to events: ``Issue comment``, ``Pull request review comment``.
- Generate a private key (``.pem``) on the App page and paste its contents
  into ``GITHUB_APP_PRIVATE_KEY`` (encode newlines as ``\\n`` for dotenv).
- Install the App into a test repo (``Install App`` on the App page), then
  note the Installation ID from the URL (``/settings/installations/<ID>``)
  if you want single-tenant mode.
- Open an issue, comment ``@<GITHUB_BOT_USERNAME> hi`` — the bot replies.
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
from chat_adapter_github import create_github_adapter
from chat_adapter_state_memory import create_memory_state

load_env()
require_env(
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_BOT_USERNAME",
)

# ``create_github_adapter`` reads ``GITHUB_APP_ID`` / ``GITHUB_PRIVATE_KEY`` /
# ``GITHUB_WEBHOOK_SECRET`` / ``GITHUB_BOT_USERNAME`` /
# ``GITHUB_INSTALLATION_ID`` from the env. We re-export
# ``GITHUB_APP_PRIVATE_KEY`` → ``GITHUB_PRIVATE_KEY`` for dotenv users who
# prefer the more explicit "APP_" prefix.
os.environ.setdefault("GITHUB_PRIVATE_KEY", os.environ["GITHUB_APP_PRIVATE_KEY"])

bot = Chat(
    user_name=os.environ["GITHUB_BOT_USERNAME"],
    adapters={"github": create_github_adapter()},
    state=create_memory_state(),
)


@bot.on_new_mention
async def on_mention(thread: Any, message: Any) -> None:
    print(f"[e2e] mention from @{message.author.user_name}: {message.text!r}")
    await thread.subscribe()
    await thread.post(
        f"hi @{message.author.user_name}, I'm subscribed to this thread — reply and I'll echo."
    )


@bot.on_subscribed_message
async def on_thread_message(thread: Any, message: Any) -> None:
    print(f"[e2e] subscribed-thread message from @{message.author.user_name}: {message.text!r}")
    await thread.post(f"echo: {message.text}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "github", port=port)
