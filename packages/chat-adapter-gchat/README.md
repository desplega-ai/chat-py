# chat-adapter-gchat

Google Chat adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-gchat`](https://github.com/vercel/chat/tree/main/packages/adapter-gchat).

Supports both HTTP-endpoint Chat apps (Workspace Add-ons event envelopes) and Pub/Sub-delivered Workspace Events subscriptions.

## Install

```bash
uv add chat-py chat-py-adapter-gchat chat-py-adapter-state-pg
```

## Auth / config

`GoogleChatAdapterConfig` — four auth modes mirror upstream:

| Mode                              | Config fields                                                         |
| --------------------------------- | --------------------------------------------------------------------- |
| Service account JSON              | `credentials` (a `ServiceAccountCredentials` dict)                    |
| Application Default Credentials   | `useApplicationDefaultCredentials=True`                               |
| Custom auth                       | `auth` (callable returning a bearer token)                            |
| Auto from env                     | none — relies on `GOOGLE_APPLICATION_CREDENTIALS` etc.                |

Additional config: `googleChatProjectNumber`, `impersonateUser`, `pubsubAudience`, `pubsubTopic`, `endpointUrl`.

## Minimal example

```python
from chat import Chat
from chat_adapter_gchat import create_google_chat_adapter
from chat_adapter_state_pg import create_postgres_state

bot = Chat(
    user_name="mybot",
    adapters={"gchat": create_google_chat_adapter(
        credentials=...,  # service account JSON dict
    )},
    state=create_postgres_state(url="postgres://localhost/chat"),
)


@bot.on_new_mention
async def greet(thread, message):
    await thread.post("Hi!")
```

Mount `bot.handle_webhook("gchat", body, headers)` under `/api/webhooks/gchat`. The adapter verifies incoming requests via bearer tokens (HTTP-endpoint apps) or Pub/Sub-signed JWTs (Workspace Events subscriptions — see `decode_pubsub_message`).

## Thread ID

Google Chat thread IDs: `gchat:spaces/{spaceId}:{base64(threadName)}`.

## Features

- HTTP-endpoint and Pub/Sub Workspace Events dispatch paths
- Space-subscription management (`create_space_subscription`, `list_space_subscriptions`, `delete_space_subscription`) with TTL-based refresh
- `UserInfoCache` for bot-to-user display-name lookups
- Card v2 translation (`card_to_google_card`) — sections, headers, buttons, images, grids
- Markdown ↔ mdast via `GoogleChatFormatConverter`

## Parity notes

- Matches upstream's cache TTLs (`SUBSCRIPTION_CACHE_TTL_MS`, `SUBSCRIPTION_REFRESH_BUFFER_MS`).
- Bearer-token verification (`verify_bearer_token`) uses the same issuer allowlist as upstream.
- Supplying both `credentials` and `useApplicationDefaultCredentials` raises `ValidationError`.

## Test

```bash
uv run pytest packages/chat-adapter-gchat

# Live — requires a configured project and credentials
GCHAT_PROJECT=... uv run pytest packages/chat-integration-tests -k gchat
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-gchat
