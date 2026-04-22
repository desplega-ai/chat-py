# chat-adapter-github

GitHub adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-github`](https://github.com/vercel/chat/tree/main/packages/adapter-github).

Handles `issue_comment` and `pull_request_review_comment` webhooks; supports PAT, single-tenant GitHub App, and multi-tenant GitHub App auth.

## Install

```bash
uv add chat chat-adapter-github chat-adapter-state-pg
```

## Auth / config

Three discriminated config variants:

| Mode                         | Required fields                       | Env var fallbacks                                   |
| ---------------------------- | ------------------------------------- | --------------------------------------------------- |
| PAT (personal access token)  | `token`                               | `GITHUB_TOKEN`                                      |
| Single-tenant App            | `appId` + `installationId` + `privateKey` | `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY` |
| Multi-tenant App             | `appId` + `privateKey`                | `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`           |

All variants additionally accept: `webhookSecret` (required for signature verify), `botUserId`, `apiUrl`, `logger`, `userName`.

## Minimal example

```python
from chat import Chat
from chat_adapter_github import create_github_adapter
from chat_adapter_state_pg import create_postgres_state

bot = Chat(
    user_name="mybot",
    adapters={"github": create_github_adapter(
        token="ghp_...",             # PAT mode
        webhookSecret="...",
    )},
    state=create_postgres_state(url="postgres://localhost/chat"),
)


@bot.on_new_mention
async def respond(thread, message):
    await thread.post("On it.")
```

Mount `bot.handle_webhook("github", body, headers)` under `/api/webhooks/github`. The handler verifies `X-Hub-Signature-256` via `verify_github_signature`.

## Thread ID

GitHub thread IDs encode `{owner}/{repo}#{issue_or_pr_number}`, optionally with a comment ID for review threads.

## Features

- HMAC-SHA256 webhook signature verify (`verify_github_signature`)
- GFM markdown rendering via `GitHubFormatConverter`
- `card_to_github_markdown` / `card_to_plain_text` fallbacks (no native card support)
- Reactions (`on_reaction`) for issue comments and PR review comments

## Parity notes

- No card or streaming support — GitHub comments are markdown-only. Cards degrade to GFM via `card_to_github_markdown`.
- Error wrapping (`handle_github_error`) maps GitHub REST errors to the shared `AdapterError` hierarchy.

## Test

```bash
uv run pytest packages/chat-adapter-github

# Live
GITHUB_TOKEN=... uv run pytest packages/chat-integration-tests -k github
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-github
