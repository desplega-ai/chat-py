# chat-adapter-linear

Linear adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-linear`](https://github.com/vercel/chat/tree/main/packages/adapter-linear).

Handles Linear comment webhooks and optional Agent Session threads; supports API-key, OAuth access-token, multi-tenant OAuth, and client-credentials auth.

## Install

```bash
uv add chat chat-adapter-linear chat-adapter-state-redis
```

## Auth / config

`LinearAdapterConfig` — discriminated union of four variants:

| Mode                         | Required fields                           | Env var fallbacks                             |
| ---------------------------- | ----------------------------------------- | --------------------------------------------- |
| API key                      | `apiKey`                                  | `LINEAR_API_KEY`                              |
| OAuth access token           | `accessToken`                             | `LINEAR_ACCESS_TOKEN`                         |
| Multi-tenant OAuth           | `clientId` + `clientSecret`               | `LINEAR_CLIENT_ID`, `LINEAR_CLIENT_SECRET`    |
| Client credentials           | `clientCredentials` (id + secret + scopes) | —                                            |

All variants additionally accept: `webhookSecret`, `mode` (`"comments"` or `"agent-sessions"`), `apiUrl`, `userName`.

## Minimal example

```python
from chat import Chat
from chat_adapter_linear import create_linear_adapter
from chat_adapter_state_redis import create_redis_state

bot = Chat(
    user_name="mybot",
    adapters={"linear": create_linear_adapter(
        apiKey="lin_api_...",
        webhookSecret="...",
    )},
    state=create_redis_state(url="redis://localhost:6379"),
)


@bot.on_new_mention
async def triage(thread, message):
    await thread.post("Investigating.")
```

Mount `bot.handle_webhook("linear", body, headers)` under `/api/webhooks/linear`.

## Thread ID

Linear thread IDs are `linear:{organizationId}:{issueId[:commentId][/agentSession=<id>]}`. Agent Session overlays nest under a comment thread — see `LinearAgentSessionThreadId` and `assert_agent_session_thread`.

## Features

- HMAC signature verify (`verify_linear_signature`)
- GraphQL-layer error unwrapping (`handle_linear_graphql_body` returns the first typed error from a Linear GraphQL response body)
- OAuth token refresh for OAuth / multi-tenant modes
- Comment mode and Agent Session mode (`LinearAdapterMode`)
- Linear-flavoured markdown ↔ mdast (`LinearFormatConverter`)
- Card → Linear markdown (`card_to_linear_markdown`)

## Parity notes

- No native cards or streaming — Linear comments are markdown-only.
- Agent Session support mirrors upstream's beta mode.

## Test

```bash
uv run pytest packages/chat-adapter-linear

# Live
LINEAR_API_KEY=... uv run pytest packages/chat-integration-tests -k linear
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-linear
