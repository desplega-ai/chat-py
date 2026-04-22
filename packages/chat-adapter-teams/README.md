# chat-adapter-teams

Microsoft Teams adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-teams`](https://github.com/vercel/chat/tree/main/packages/adapter-teams).

Talks to the Bot Framework REST API directly via `httpx` + `msal`. Upstream uses `@microsoft/teams.apps`; no stable Python equivalent exists, so the adapter is implemented against the REST surface.

## Install

```bash
uv add chat-py chat-py-adapter-teams chat-py-adapter-state-redis
```

## Auth / config

`TeamsAdapterConfig` supports three auth modes:

| Mode                   | Fields                                       | Env var fallbacks                                           |
| ---------------------- | -------------------------------------------- | ----------------------------------------------------------- |
| Client secret          | `appId` + `appPassword`                      | `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, `TEAMS_APP_TENANT_ID` |
| Federated identity     | `federated` (workload identity / managed ID) | `TEAMS_APP_ID`, `TEAMS_APP_TENANT_ID`                       |
| Certificate (PEM)      | `certificate` — **not implemented**; raises `ValidationError` | —                                      |

Additional config: `appType` (`"MultiTenant"` or `"SingleTenant"`), `dialogOpenTimeoutMs`, `apiUrl`.

## Minimal example

```python
from chat import Chat
from chat_adapter_teams import create_teams_adapter
from chat_adapter_state_redis import create_redis_state

bot = Chat(
    user_name="mybot",
    adapters={"teams": create_teams_adapter()},  # reads TEAMS_APP_ID etc.
    state=create_redis_state(url="redis://localhost:6379"),
)


@bot.on_new_message
async def reply(thread, message):
    await thread.post("Received.")
```

Mount `bot.handle_webhook("teams", body, headers)` under `/api/webhooks/teams`. The webhook handler verifies Bot Framework-signed JWTs via `verify_bearer_token`.

## Thread ID

Teams thread IDs: `teams:{base64(conversationId)}:{base64(serviceUrl)}`.

## Parity notes

- JWT verification uses the same JWKS + issuer allowlist as upstream.
- Adaptive Card translation (`card_to_adaptive_card`) targets the same schema as upstream's converter.
- `TeamsFormatConverter` produces the same markdown↔mdast output.
- **Known gaps** (v0.1.0 — raises `NotImplementedError`):
  - Microsoft Graph API conversation reader (`read_thread`)
  - Certificate-based auth (`certificate` config field)
- Live event dispatch (`@microsoft/teams.apps`) is not ported; `handle_webhook` is a JWT-verifying shim. Full dispatch lands with the async `Adapter` protocol in core.

## Test

```bash
uv run pytest packages/chat-adapter-teams

# Live — requires an app registration
TEAMS_APP_ID=... TEAMS_APP_PASSWORD=... uv run pytest packages/chat-integration-tests -k teams
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-teams
