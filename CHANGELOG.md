# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`chat-py` is a Python port of [`vercel/chat`](https://github.com/vercel/chat). This release tracks upstream `chat@4.26.0`.

## [Unreleased]

### Added

- Documentation polish for v0 release: filled per-package READMEs, expanded CHANGELOG with known gaps (DES-192).

## [0.1.0] — 2026-04-22

Initial Python port of [`vercel/chat`](https://github.com/vercel/chat) v4.26.0. `uv`-native workspace, Python 3.13, async-first.

### Added

#### Core

- **`chat`** (DES-174, DES-177, DES-181) — core SDK: `Chat` entrypoint, `Thread` / `Message` / `Channel` / `Plan` primitives, `Adapter` / `State` protocols, card builders (`Card`, `Section`, `Button`, `Fields`, `Image`, `Actions`, …), mdast helpers, streaming (`from_full_stream`, `PostableObject`), reviver (`chat:Message` / `chat:Thread` / `chat:Channel` / `chat:Plan` discriminators), and the `mock_adapter` test utilities.

#### Platform adapters

- **`chat-adapter-shared`** (DES-175, DES-176) — shared helpers: `extract_card`, `extract_files`, `to_buffer`, `card_to_fallback_text`, `map_button_style`, `render_gfm_table`, `create_emoji_converter`, and the common `AdapterError` / `AdapterRateLimitError` / `AuthenticationError` / `NetworkError` / `PermissionError` / `ResourceNotFoundError` / `ValidationError` hierarchy.
- **`chat-adapter-slack`** (DES-178, DES-179, DES-180) — Events API, Socket Mode, OAuth distribution, native Block Kit translation, native streaming, modals, slash commands, reactions, encrypted installation tokens, deep-link parsing.
- **`chat-adapter-teams`** (DES-185) — Bot Framework REST path via `httpx` + `msal`; JWT verification against the Bot Framework JWKS; Adaptive Card translation; markdown ↔ mdast; client-secret and federated-identity auth modes.
- **`chat-adapter-gchat`** (DES-186) — HTTP-endpoint and Pub/Sub dispatch paths; space-subscription lifecycle; Card v2 translation; `GoogleChatFormatConverter`; four auth variants (service account, ADC, custom, auto-from-env).
- **`chat-adapter-discord`** (DES-184) — HTTP Interactions path with Ed25519 signature verification; REST post / edit / delete / reactions / typing; button, link, and select-menu card translation; slash-command parser with deferred-response context.
- **`chat-adapter-github`** (DES-187) — `issue_comment` and `pull_request_review_comment` webhooks; HMAC-SHA256 verify; PAT, single-tenant App, multi-tenant App auth variants; GFM card rendering.
- **`chat-adapter-linear`** (DES-188) — Comment and Agent Session modes; HMAC signature verify; API-key, OAuth, multi-tenant OAuth, client-credentials auth; Linear-flavoured markdown converter.

#### State adapters

- **`chat-adapter-state-memory`** (DES-189) — in-memory dict-backed adapter; full `State` protocol surface (locks, subscriptions, queues, cached values).
- **`chat-adapter-state-redis`** (DES-190) — `redis.asyncio`-backed adapter; `SET NX PX` locks, TTL-based queues and cache.
- **`chat-adapter-state-ioredis`** (DES-190) — thin `RedisStateAdapter` subclass. Only difference vs `state-redis` is the `ioredis_<ts>_<hex>` lock-token prefix, matching upstream byte-for-byte so a Redis instance can be shared with TypeScript clients.
- **`chat-adapter-state-pg`** (DES-191) — `asyncpg`-backed adapter; auto-migrated `chat_locks` / `chat_queues` / `chat_subscriptions` / `chat_cache` tables; lock tokens shaped `pg_<ts>_<hex>`.

#### Integration tests

- **`chat-integration-tests`** (DES-191) — per-state-backend dispatch tests (memory, Redis, ioredis, Postgres), serialization round-trips, webhook error paths. Live-service suites are opt-in via env vars (`SLACK_TOKEN`, `REDIS_URL`, `POSTGRES_URL`, …) with `require_backend` helper that `pytest.skip`s when the backend isn't configured.

#### Docs & infra

- `uv`-native workspace (`pyproject.toml` `[tool.uv.workspace]`) with 15 members.
- Python 3.13 baseline, async-first.
- Ruff (lint + format) + mypy strict (on `packages/chat/src`) + pytest with `asyncio_mode = "auto"`.
- CI via GitHub Actions (`.github/workflows/ci.yml`).
- Per-package READMEs, root README, CONTRIBUTING, docs/parity.md, docs/getting-started.md (DES-192).

### Known gaps

Documented explicitly so downstream users don't waste time debugging them:

- **`chat` core** — 32 `mypy --strict` errors remain in `packages/chat/src`. They do not affect runtime behaviour; type-annotation cleanup lands in v0.2.0.
- **`chat-adapter-slack`** — a subset of ported tests carry over pre-existing failures from upstream's test suite. These were intentionally kept visible rather than skipped, so behaviour stays aligned with upstream.
- **`chat-adapter-teams`** — two fields raise `NotImplementedError`:
  - The Microsoft Graph API conversation reader (`read_thread`) — upstream relies on `@microsoft/teams.apps`'s live dispatch, which has no stable Python equivalent in v0.
  - Certificate-based auth (`certificate` config) — scaffolded but not wired; passing it raises `ValidationError`.
- **`chat-adapter-telegram`** — **placeholder stub.** Import is safe; no adapter logic yet. Tracked by DES-182.
- **`chat-adapter-whatsapp`** — **placeholder stub.** Import is safe; no adapter logic yet. Tracked by DES-183.
- **Live dispatch in the `Adapter` protocol** — the async event-dispatch half of the Adapter protocol lands in core in v0.2.0. Adapters currently expose `handle_webhook` as a thin verify-and-parse shim; full handler routing flows through `Chat`.

### Linear tracking

| Linear    | Area                                          |
| --------- | --------------------------------------------- |
| DES-174   | chat core (part A)                            |
| DES-175   | chat-adapter-shared (part A)                  |
| DES-176   | chat-adapter-shared (part B)                  |
| DES-177   | chat core (part B — streaming, mdast, cards)  |
| DES-178   | chat-adapter-slack (adapter)                  |
| DES-179   | chat-adapter-slack (cards + modals)           |
| DES-180   | chat-adapter-slack (markdown)                 |
| DES-181   | chat core (serialization / reviver)           |
| DES-184   | chat-adapter-discord                          |
| DES-185   | chat-adapter-teams                            |
| DES-186   | chat-adapter-gchat                            |
| DES-187   | chat-adapter-github                           |
| DES-188   | chat-adapter-linear                           |
| DES-189   | chat-adapter-state-memory                     |
| DES-190   | chat-adapter-state-redis + state-ioredis      |
| DES-191   | chat-integration-tests + chat-adapter-state-pg |
| DES-192   | v0 docs / release polish                      |
| DES-182   | chat-adapter-telegram (pending — stub shipped) |
| DES-183   | chat-adapter-whatsapp (pending — stub shipped) |

[Unreleased]: https://github.com/desplega-ai/chat-py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/desplega-ai/chat-py/releases/tag/v0.1.0
