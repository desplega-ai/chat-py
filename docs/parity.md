# Upstream → Python parity map

This document tracks every deliberate difference between [`vercel/chat`](https://github.com/vercel/chat) (TypeScript) and `chat-py` (Python). The goal of `chat-py` is 1:1 behavioral parity — any entry here is a justified exception.

## Language translations (every file)

| Upstream                | Python                                      |
| ----------------------- | ------------------------------------------- |
| `interface Foo`         | `Protocol` or `TypedDict`                   |
| `type Foo = A \| B`     | `Foo = A \| B` (PEP 604 union)              |
| `readonly`              | `Final[...]` / `frozen=True` dataclass      |
| `Date`                  | `datetime.datetime` (timezone-aware, UTC)   |
| `Promise<T>`            | `Awaitable[T]`                              |
| `async function`        | `async def`                                 |
| camelCase methods       | snake_case methods                          |
| `null`                  | `None`                                      |
| `undefined`             | missing field (TypedDict `NotRequired`)     |
| `Buffer`                | `bytes`                                     |
| JSON log structure      | identical                                   |

## Public API renames

| Upstream                               | Python                                     |
| -------------------------------------- | ------------------------------------------ |
| `new Chat({...})`                      | `Chat(...)`                                |
| `chat.onNewMention(fn)`                | `@chat.on_new_mention` or `chat.on_new_mention(fn)` |
| `chat.onNewMessage(pattern, fn)`       | `chat.on_new_message(pattern)(fn)`         |
| `thread.post(...)`                     | `await thread.post(...)`                   |
| `<Card title="...">...</Card>`         | `Card(title="...", children=[...])`        |
| `<Button id="..." />`                  | `Button(id="...")`                         |
| `createSlackAdapter()`                 | `create_slack_adapter()`                   |
| `createRedisState()`                   | `create_redis_state()`                     |

## Package renames

| Upstream                  | chat-py                            | Notes                                 |
| ------------------------- | ---------------------------------- | ------------------------------------- |
| `chat`                    | `chat`                             | same name; PyPI                        |
| `@chat-adapter/shared`    | `chat-adapter-shared`              | no npm scopes in PyPI                 |
| `@chat-adapter/slack`     | `chat-adapter-slack`               |                                       |
| `@chat-adapter/discord`   | `chat-adapter-discord`             |                                       |
| `@chat-adapter/teams`     | `chat-adapter-teams`               |                                       |
| `@chat-adapter/gchat`     | `chat-adapter-gchat`               |                                       |
| `@chat-adapter/telegram`  | `chat-adapter-telegram`            |                                       |
| `@chat-adapter/github`    | `chat-adapter-github`              |                                       |
| `@chat-adapter/linear`    | `chat-adapter-linear`              |                                       |
| `@chat-adapter/whatsapp`  | `chat-adapter-whatsapp`            |                                       |
| `@chat-adapter/state-memory`  | `chat-adapter-state-memory`    |                                       |
| `@chat-adapter/state-redis`   | `chat-adapter-state-redis`     |                                       |
| `@chat-adapter/state-ioredis` | `chat-adapter-state-ioredis`   | Python has one redis client; both wrap `redis.asyncio` with different API flavors for parity |
| `@chat-adapter/state-pg`  | `chat-adapter-state-pg`            |                                       |

## Implementation-level differences

### mdast AST

The TS SDK uses [`mdast`](https://github.com/syntax-tree/mdast) via `unified` + `remark-parse` + `remark-gfm` + `remark-stringify`. We mirror the dict shape exactly (e.g. `{"type": "root", "children": [...]}`) using [`mistune`](https://mistune.lepture.com) as the underlying parser and a hand-written stringifier.

If you serialize a message from TS and deserialize it in Python (or vice versa), the `formatted` field round-trips without loss.

### JSX cards

Python has no JSX. The TypeScript JSX-based card API:

```tsx
<Card title="Order">
  <Text>Total $50</Text>
  <Button id="ok">OK</Button>
</Card>
```

becomes, in Python:

```python
Card(title="Order", children=[
    CardText("Total $50"),
    Button(id="ok", label="OK"),
])
```

The names, props, and card output are identical.

### Slack adapter — email autolinks

Upstream's markdown parser (`remark-gfm`) auto-links bare email addresses in markdown input, so `{markdown: "Contact user@example.com"}` becomes `"Contact <mailto:user@example.com|user@example.com>"`. Our `mistune`-based parser does not auto-link emails, so the email passes through untouched (`"Contact user@example.com"`). The security-critical invariant is identical: bare ``@word`` inside an email is NOT rewritten as a Slack mention.

### Teams adapter

The TypeScript adapter uses `@microsoft/teams.api` / `@microsoft/teams.apps` (Microsoft's Teams v2 SDK). The Python Teams v2 SDK is not stable, so `chat-adapter-teams` talks to the Bot Framework REST API directly via `httpx` + `msal` for authentication. Behavioral parity is preserved; only the transport differs.

### Redis state adapters

Python has one first-class Redis client — `redis-py` with its `redis.asyncio` submodule. Both `chat-adapter-state-redis` and `chat-adapter-state-ioredis` wrap `redis.asyncio` but expose different API flavors matching the TypeScript `redis@5` and `ioredis` packages. Choose whichever shape you prefer.

### Serialization reviver

`chat.reviver` in Python is equivalent to `reviver` in TS. When JSON data contains `_type: "chat:Message"` etc., the reviver reconstructs the corresponding class. Cross-language compatible.

### Workflow serde

`@workflow/serde`'s `WORKFLOW_SERIALIZE` / `WORKFLOW_DESERIALIZE` symbols become Python `__chat_serialize__` / `__chat_deserialize__` methods (mirror of `__reduce__` / `__setstate__` but scoped to this SDK's serialization path).

## Dispatch surface

Every adapter's `handle_webhook` + outbound message surface, as of the DES-196 port. States:

- `full` — implemented and exercised by `chat-integration-tests/test_dispatch_memory.py`.
- `stub` — declared on the adapter, raises `chat.errors.NotImplementedError` at call site (see "Deliberate NotImplementedError stubs" below).
- `n/a (upstream limit)` — upstream TypeScript does not implement this method either; parity preserved.

| adapter   | handle_webhook | post | edit | delete | react | streaming | notes                                                            |
| --------- | -------------- | ---- | ---- | ------ | ----- | --------- | ---------------------------------------------------------------- |
| slack     | full           | full | full | full   | full  | full      | HTTP Events API + Socket Mode (Phase 1 + Phase 2 of DES-196).    |
| gchat     | full           | full | full | full   | full  | full      | HTTP webhook + Pub/Sub push (Phase 3 of DES-196).                |
| discord   | full           | full | full | full   | full  | full      | HTTP interactions; modals stubbed (Discord has no modal surface). |
| github    | full           | full | full | full   | stub  | full      | Issue-comment reactions via GitHub reactions API (limited set).  |
| teams     | full           | full | full | full   | stub  | full      | 7 deliberate stubs — see below.                                  |
| linear    | full           | full | full | full   | stub  | full      | `add_reaction` / `remove_reaction` stubbed (Linear has no surface). |
| telegram  | full           | full | full | full   | full  | full      | 1 deliberate stub — see below.                                   |
| whatsapp  | full           | full | full | full   | full  | full      | DM-only (WhatsApp Cloud API); 2 deliberate stubs — see below.    |

### Deliberate NotImplementedError stubs

These methods are declared on the adapter but raise `chat.errors.NotImplementedError` on call. They are pinned by tests in each adapter's `test_unsupported_features.py` (or equivalent) so behaviour can't silently change.

- **`chat-adapter-discord`** — 1 site in `packages/chat-adapter-discord/src/chat_adapter_discord/adapter.py`:
  - `open_modal` — Discord has no standalone modal-open surface; modals are delivered as responses to an interaction (`APPLICATION_MODAL`). Upstream does not wire `open_modal` for Discord either; we raise `chat.NotImplementedError(feature="modals")` to satisfy the Protocol.
- **`chat-adapter-gchat`** — 1 site in `packages/chat-adapter-gchat/src/chat_adapter_gchat/adapter.py` (approx. `:1115`):
  - `open_modal` — Google Chat has no Slack-style modal; use a Card v2 response instead. Raises `chat.NotImplementedError(feature="modals")` to satisfy the Protocol.
- **`chat-adapter-github`** — 4 sites in `packages/chat-adapter-github/src/chat_adapter_github/adapter.py`:
  - `open_dm` — GitHub has no DM surface; issues and PRs are always repo-scoped. Raises `chat.NotImplementedError(feature="open_dm")`.
  - `open_modal` — GitHub has no modal surface; use issue comments or PR review comments for interactive flows. Raises `chat.NotImplementedError(feature="open_modal")`.
  - `post_channel_message` — GitHub has no channel-level post surface; messages are always thread-scoped (issue or PR). Raises `chat.NotImplementedError(feature="post_channel_message")`.
  - `fetch_channel_messages` — GitHub has no flat channel-message stream; comments belong to individual issues/PRs. Raises `chat.NotImplementedError(feature="fetch_channel_messages")`.
- **`chat-adapter-teams`** — 7 sites in `packages/chat-adapter-teams/src/chat_adapter_teams/adapter.py` (approx. `:444-495`):
  - `read_thread` — upstream relies on `@microsoft/teams.apps`'s live dispatch which has no stable Python equivalent.
  - Certificate-based auth (`certificate` config) — scaffolded but not wired.
  - 5 reaction-related paths on Teams' Bot Framework REST transport — Teams does not expose message reactions through the REST API.
- **`chat-adapter-whatsapp`** — 7 sites in `packages/chat-adapter-whatsapp/src/chat_adapter_whatsapp/adapter.py`:
  - `edit_message` — WhatsApp Cloud API has no edit endpoint; callers must send a new message. Raises `chat.NotImplementedError(feature="editMessage")`.
  - `delete_message` — WhatsApp Cloud API has no delete endpoint. Raises `chat.NotImplementedError(feature="deleteMessage")`.
  - `post_channel_message` / `fetch_channel_info` / `fetch_channel_messages` / `list_threads` — WhatsApp Cloud API is 1:1 DM-only; there is no channel surface. Each raises `chat.NotImplementedError` with a matching `feature` attribute.
  - `open_modal` — WhatsApp has no modal surface; use interactive messages (buttons / list) instead. Raises `chat.NotImplementedError(feature="open_modal")`.
- **`chat-adapter-telegram`** — 1 site in `packages/chat-adapter-telegram/src/chat_adapter_telegram/adapter.py` (approx. `:584`):
  - Telegram channel admin surface (upstream parity: same stub state).

## Entrypoints not yet ported

(None at initial release — 100% port is the goal. This section gets populated only if we defer something.)
