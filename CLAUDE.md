# CLAUDE.md — Guidance for AI Coding Agents

This file provides guidance to Claude Code (claude.ai/code), Cursor, and other AI coding agents when working with `chat-py`.

## About this repo

`chat-py` is a **Python port of [vercel/chat](https://github.com/vercel/chat)**. It's a `uv`-managed workspace with one core package (`chat`) and a set of platform/state adapters. The goal is 1:1 behavioral parity with upstream TypeScript, with Pythonic casing (`snake_case` methods, `CamelCase` types).

## Source of truth

When in doubt, read upstream:

- Repo: https://github.com/vercel/chat
- Docs: https://chat-sdk.dev
- Core package: https://github.com/vercel/chat/tree/main/packages/chat
- Adapter packages: https://github.com/vercel/chat/tree/main/packages

If behavior in `chat-py` diverges from upstream, that's a bug unless it's documented in `docs/parity.md`.

## Releases

Full procedure in [`docs/releasing.md`](docs/releasing.md). The short version:

- 14 of 15 packages publish under the `chat-py*` PyPI prefix; `chat-py-integration-tests` is workspace-only.
- Import names (`import chat`, `import chat_adapter_slack`, …) are decoupled from PyPI dist names — don't confuse the two.
- Always dry-run against TestPyPI before `uv publish`-ing to real PyPI. PyPI is write-once per version.
- `CHANGELOG.md` drives release notes; no per-version release doc lives in the repo.

## Common tasks

### Manual E2E scripts (examples/e2e/)

Per-adapter, per-scenario scripts for hitting real provider APIs. They are **not** pytest tests. Each script is a self-contained `uv run python` invocation with its own docstring covering:

- required env vars (the script `sys.exit()`s with a clear message if any are unset)
- provider app/bot setup (scopes, events, webhook URL)
- run command

Shape:

```bash
uv sync --group e2e                                # fastapi + uvicorn + python-dotenv
uv run python examples/e2e/slack/echo.py           # in one terminal
ngrok http 8000                                    # in another; paste URL into provider
```

Scripts read `<repo-root>/.env` via `python-dotenv`; `.env` is gitignored. Full how-to: [`examples/e2e/README.md`](examples/e2e/README.md). Add new scenarios by copying an existing script — shared env/FastAPI glue lives in `examples/e2e/_common.py`.

### Running the full validation suite

```bash
uv sync --all-packages --dev
uv run ruff check packages/
uv run ruff format --check packages/
uv run mypy packages/chat/src
uv run pytest packages/
```

Always run this before declaring a task complete.

### Running tests for a single package

```bash
uv run pytest packages/chat
uv run pytest packages/chat-adapter-slack
uv run pytest packages/chat-integration-tests
```

### Running a single test

```bash
uv run pytest packages/chat -k test_thread_post
uv run pytest packages/chat/src/chat_test.py::test_handle_mention
```

### Adding a dependency

Use `uv add` inside the package directory, not manual `pyproject.toml` edits:

```bash
cd packages/chat-adapter-slack
uv add slack-sdk
```

For dev dependencies at the workspace root:

```bash
uv add --dev pytest-asyncio
```

## Code style

- **Format with `ruff`** (configured in root `pyproject.toml`) — `uv run ruff format packages/`.
- **Lint with `ruff`** — `uv run ruff check packages/`.
- **Type-check core with `mypy --strict`** — `uv run mypy packages/chat/src`.
- Prefer explicit imports: `from chat.types import Thread` over `from chat import *`.
- Use `Protocol` for adapter interfaces (matches TS `interface`).
- Use `TypedDict` / `NotRequired` for serialized payloads (matches TS `interface` with `?`).
- Use `dataclass(slots=True)` for internal structs where immutability isn't required; `frozen=True` where it is.

## Architecture

Identical to upstream (see upstream CLAUDE.md for the full picture). Key translations:

| TypeScript concept        | Python equivalent                              |
| ------------------------- | ---------------------------------------------- |
| `interface Foo`           | `class Foo(Protocol)` or `TypedDict`           |
| `type Foo = A \| B`       | `Foo = A \| B` (PEP 604)                       |
| `class Chat`              | `class Chat`                                   |
| `async onNewMention(fn)`  | `@bot.on_new_mention` decorator                |
| `thread.post()`           | `await thread.post()`                          |
| `mdast.Root` (remark)     | `MdastRoot` dict — same shape as remark output |
| `JSX <Card>...</Card>`    | `Card(children=[...])` builder call            |
| Platform webhook handler  | `await chat.handle_webhook(name, body, headers)` — returns `(status, headers, body)` tuple |

### Thread ID format

Same as upstream: `{adapter}:{channel}:{thread}`.

- Slack: `slack:C123ABC:1234567890.123456`
- Teams: `teams:{base64(conversationId)}:{base64(serviceUrl)}`
- Google Chat: `gchat:spaces/ABC123:{base64(threadName)}`

### Message handling flow

1. Platform sends webhook to `/api/webhooks/{platform}`.
2. Adapter verifies request, parses message, calls `chat.handle_incoming_message()`.
3. Chat class acquires lock on thread, then:
   - Checks if thread is subscribed → calls `on_subscribed_message` handlers
   - Checks for @mention → calls `on_new_mention` handlers
   - Checks message patterns → calls matching `on_new_message` handlers
4. Handler receives `Thread` and `Message` objects.

## Test utilities

`packages/chat/src/chat/mock_adapter.py` provides shared test utilities — the Python equivalent of `packages/chat/src/mock-adapter.ts`:

- `create_mock_adapter(name)` — creates an in-process mock adapter with `unittest.mock.AsyncMock` method stubs
- `create_mock_state()` — creates a mock state adapter with working in-memory subscriptions, locks, and cache
- `create_test_message(id, text, **overrides)` — creates a test `Message` object
- `mock_logger` — a `Logger` that captures all log calls

```python
from chat.mock_adapter import create_mock_adapter, create_mock_state, create_test_message

adapter = create_mock_adapter("slack")
state = create_mock_state()
message = create_test_message("msg-1", "Hello world")
```

## Porting notes

When porting code from upstream TS to Python:

1. **Rename members to `snake_case`** (but keep classes `CamelCase`). `onNewMention` → `on_new_mention`; `threadId` → `thread_id`; `SerializedChannel` stays `SerializedChannel`.
2. **Keep the module layout.** `packages/chat-sdk/src/chat.ts` maps to `packages/chat/src/chat/chat.py`. Same file names, same split.
3. **Dict-based AST.** Don't wrap mdast nodes in custom classes — keep them as `dict[str, Any]` with the same `type`/`children`/`value` keys so serialized data stays cross-language compatible.
4. **Dates.** Upstream uses `Date`; we use `datetime.datetime` with timezone-aware UTC. Serialization is ISO 8601.
5. **Errors.** Keep the class hierarchy: `ChatError`, `LockError`, `NotImplementedError`, `RateLimitError`. Error names and messages should match upstream.
6. **Reviver.** Port `reviver.ts` to `chat/serialization.py`; reconstructs typed objects from JSON payloads based on `_type` discriminators (`chat:Message`, `chat:Thread`, `chat:Channel`, `chat:Plan`).

## Do not

- ❌ Pin to pre-release or deprecated Python libraries.
- ❌ Introduce new concepts that don't exist upstream (unless documented in `docs/parity.md`).
- ❌ Skip tests for ported behavior — the TS test coverage is our floor.
- ❌ Mix sync and async APIs. Everything platform-facing is async.

## Upstream tracking

We track upstream closely. When upstream ships a change that affects a ported package, open an issue titled `Upstream sync: <area>` linking the upstream PR, and port within the same minor cycle if possible.
