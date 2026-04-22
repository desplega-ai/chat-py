"""Linear E2E — echo scenario.

What this tests
---------------
- Bot receives Linear ``Comment`` webhooks (signed with ``Linear-Signature``
  HMAC-SHA256) → dispatch fires ``on_new_mention`` when the comment body
  contains ``@<bot-username>``.
- Outbound ``thread.post(...)`` posts a Linear comment back on the same
  issue via ``commentCreate`` GraphQL mutation.

Required env vars
-----------------
- ``LINEAR_API_KEY``              Personal API key or OAuth access token
                                  (scope: ``read`` + ``write`` + ``comments:create``).
- ``LINEAR_WEBHOOK_SECRET``       The secret configured on the Linear
                                  webhook (used to verify
                                  ``Linear-Signature``).

Optional
- ``LINEAR_BOT_USERNAME``          Defaults to ``linear-bot``. The bot's
                                   Linear profile slug — mentions are
                                   matched case-insensitively against this.
- ``E2E_PORT``                     Webhook server port (default ``8000``).

Run
---
::

    uv sync --group e2e
    uv run python examples/e2e/linear/echo.py

In a second terminal::

    ngrok http 8000
    # paste the https://…ngrok.app URL + ``/api/webhooks/linear`` into
    # Linear → Settings → API → Webhooks → New webhook.

Linear setup (once)
-------------------
1. **Create an API key or OAuth app** (Linear → Settings → API).

   - For single-workspace testing: create a **Personal API key** and set
     ``LINEAR_API_KEY`` to it (``lin_api_…``).
   - For multi-tenant usage: create an **OAuth application** and use the
     ``LINEAR_CLIENT_ID`` / ``LINEAR_CLIENT_SECRET`` env vars instead —
     see :mod:`chat_adapter_linear` for the full config matrix.

2. **Register a webhook** (Linear → Settings → API → Webhooks)

   - URL: ``https://<ngrok>.ngrok.app/api/webhooks/linear``.
   - Resource types: enable ``Comments`` (and optionally ``Agent session
     events`` if running in ``agent-sessions`` mode).
   - Copy the generated **signing secret** → ``LINEAR_WEBHOOK_SECRET``.

3. **Test**

   - Open any Linear issue in the workspace.
   - Post a comment mentioning the bot user: ``@chat-py-bot hello``.
   - The script prints every inbound comment and replies with
     ``echo: <text>``.

Notes
-----
- ``add_reaction`` is supported (calls Linear's ``reactionCreate``
  GraphQL mutation). ``remove_reaction`` raises
  :class:`chat.NotImplementedError(feature="removeReaction")` — Linear's
  GraphQL surface requires a reaction-id lookup upstream does not
  implement either. See ``docs/parity.md`` under *Deliberate
  NotImplementedError stubs*.
- Subscription is implicit — Linear webhooks deliver every issue comment
  once the webhook is registered; ``thread.subscribe()`` is a no-op.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make ``examples/e2e/_common.py`` importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _common import load_env, require_env, run_webhook_server  # noqa: E402
from chat import Chat  # noqa: E402
from chat_adapter_linear import create_linear_adapter  # noqa: E402
from chat_adapter_state_memory import create_memory_state  # noqa: E402

load_env()
require_env("LINEAR_API_KEY", "LINEAR_WEBHOOK_SECRET")

bot = Chat(
    user_name=os.environ.get("LINEAR_BOT_USERNAME", "linear-bot"),
    adapters={"linear": create_linear_adapter()},
    state=create_memory_state(),
)


@bot.on_new_mention
async def on_mention(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] mention from {message.author.user_id}: {message.text!r}")
    await thread.post(
        f"hi {message.author.full_name or message.author.user_name}, "
        "I'm listening — mention me again and I'll echo."
    )


@bot.on_new_message
async def on_any_message(thread, message):  # type: ignore[no-untyped-def]
    print(f"[e2e] comment: {message.text!r}")


if __name__ == "__main__":
    port = int(os.environ.get("E2E_PORT", "8000"))
    run_webhook_server(bot, "linear", port=port)
