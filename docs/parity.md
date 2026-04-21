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

### Teams adapter

The TypeScript adapter uses `@microsoft/teams.api` / `@microsoft/teams.apps` (Microsoft's Teams v2 SDK). The Python Teams v2 SDK is not stable, so `chat-adapter-teams` talks to the Bot Framework REST API directly via `httpx` + `msal` for authentication. Behavioral parity is preserved; only the transport differs.

### Redis state adapters

Python has one first-class Redis client — `redis-py` with its `redis.asyncio` submodule. Both `chat-adapter-state-redis` and `chat-adapter-state-ioredis` wrap `redis.asyncio` but expose different API flavors matching the TypeScript `redis@5` and `ioredis` packages. Choose whichever shape you prefer.

### Serialization reviver

`chat.reviver` in Python is equivalent to `reviver` in TS. When JSON data contains `_type: "chat:Message"` etc., the reviver reconstructs the corresponding class. Cross-language compatible.

### Workflow serde

`@workflow/serde`'s `WORKFLOW_SERIALIZE` / `WORKFLOW_DESERIALIZE` symbols become Python `__chat_serialize__` / `__chat_deserialize__` methods (mirror of `__reduce__` / `__setstate__` but scoped to this SDK's serialization path).

## Entrypoints not yet ported

(None at initial release — 100% port is the goal. This section gets populated only if we defer something.)
