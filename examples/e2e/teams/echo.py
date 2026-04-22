"""Microsoft Teams E2E — echo scenario.

What this tests
---------------
- Bot receives Bot Framework ``message`` activities via the Azure Bot
  Service messaging endpoint → dispatch fires ``on_new_mention`` /
  ``on_subscribed_message``.
- Bot Framework JWT bearer verification round-trips (JWKS fetched from
  ``https://login.botframework.com/v1/.well-known/keys``).
- Outbound ``post_message`` uses Bot Framework REST with an MSAL-acquired
  bot token (appId + appPassword, or federated credentials).

Required env vars
-----------------
- ``TEAMS_APP_ID``             Azure App Registration (Application ID)
- ``TEAMS_APP_PASSWORD``       Client secret from the same App Registration
- ``TEAMS_TENANT_ID``          Azure tenant ID (``TEAMS_APP_TENANT_ID`` also accepted)

Optional
- ``TEAMS_API_URL``            override Bot Framework REST base (e.g. for EU regions)
- ``E2E_PORT``                 defaults to 8000

Run
---
::

    uv sync --group e2e
    uv run python examples/e2e/teams/echo.py

In a second terminal::

    ngrok http 8000
    # paste the https://…ngrok.app URL + ``/api/webhooks/teams`` into
    # Azure Bot → Configuration → Messaging endpoint

Azure / Teams setup (once)
--------------------------
1. **App Registration** (``portal.azure.com`` → Entra ID → App registrations)

   - Create a new ``MultiTenant`` (or ``SingleTenant``) app.
   - Copy **Application (client) ID** → ``TEAMS_APP_ID``.
   - Copy **Directory (tenant) ID** → ``TEAMS_TENANT_ID``.
   - Certificates & secrets → **New client secret** → ``TEAMS_APP_PASSWORD``.

2. **Azure Bot resource** (``portal.azure.com`` → Create a resource → "Azure Bot")

   - Pick the same Microsoft App ID from step 1 (use existing).
   - Configuration → **Messaging endpoint**: ``https://<ngrok>.ngrok.app/api/webhooks/teams``.
   - Channels → **Microsoft Teams** → enable.

3. **Teams app manifest**

   - Developer Portal (``dev.teams.microsoft.com``) → Apps → New app.
   - Basic info: fill name + descriptions.
   - App features → Bot → use existing bot ID (same Application ID).
   - Scopes: ``personal``, ``team``, ``groupChat`` (as desired).
   - Package → Download → **Upload to Teams** (Teams → Apps → Manage → Upload).

4. **Test**

   - Install the app into a Team, then ``@mention`` your bot in any channel
     or start a 1:1 chat. The script prints every inbound activity and
     echoes back.

Notes
-----
- Subscription is implicit on Teams — once the bot is added to a
  conversation it receives every message. ``thread.subscribe()`` is a no-op.
- Reactions and Graph-reader methods (``fetch_messages``, ``list_threads``,
  …) raise :class:`chat.NotImplementedError` — see ``docs/parity.md``
  under *Deliberate NotImplementedError stubs*.
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
from chat_adapter_teams import create_teams_adapter

load_env()
require_env("TEAMS_APP_ID", "TEAMS_APP_PASSWORD", "TEAMS_TENANT_ID")

# ``TEAMS_APP_TENANT_ID`` is the canonical env var the adapter reads;
# accept ``TEAMS_TENANT_ID`` as a friendlier alias.
if "TEAMS_APP_TENANT_ID" not in os.environ and "TEAMS_TENANT_ID" in os.environ:
    os.environ["TEAMS_APP_TENANT_ID"] = os.environ["TEAMS_TENANT_ID"]

bot = Chat(
    user_name="chat-py-e2e",
    adapters={"teams": create_teams_adapter()},  # picks up TEAMS_* env vars
    state=create_memory_state(),
)


@bot.on_new_mention
async def on_mention(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] mention from {message.author.user_id}: {message.text!r}")
    await thread.subscribe()
    await thread.post(f"hi {message.author.full_name}, I'm listening — reply and I'll echo.")


@bot.on_subscribed_message
async def on_thread_message(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] subscribed-thread message: {message.text!r}")
    await thread.post(f"echo: {message.text}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "teams", port=port)
