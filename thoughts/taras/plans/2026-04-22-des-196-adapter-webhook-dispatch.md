---
ticket: DES-196
title: "[chat-py] implement adapter webhook dispatch (Part B)"
status: in-progress
created: 2026-04-22
last_updated: 2026-04-22
last_updated_by: claude (phase 0)
author: taras
autonomy: critical
style: tdd
commit_granularity: per-feature
rollback_strategy: ask-for-guidance
upstream_ref: https://github.com/vercel/chat
related_ticket: https://linear.app/desplega-labs/issue/DES-196/chat-py-implement-adapter-webhook-dispatch-part-b
parent_project: https://linear.app/desplega-labs/project/chat-sdk-port-112924072c77/overview
---

# DES-196 — Adapter webhook dispatch (Part B)

## Summary

Close the last critical gap blocking `chat-py` v0.1.0 publish: adapter event dispatch.

**Current state (verified against source).**
- `Chat.handle_webhook(adapter, ...)` delegates to `adapter.handle_webhook(request, options)` — see `packages/chat/src/chat/chat.py:195`.
- **Slack** (`packages/chat-adapter-slack/src/chat_adapter_slack/adapter.py`, 546 LOC) and **GChat** (`packages/chat-adapter-gchat/src/chat_adapter_gchat/adapter.py`, 518 LOC) are Part-A only: they expose signature verification, thread-ID codec, cards, markdown, config + factory — **no** `handle_webhook` / `post_message` / `edit_message` / `delete_message` / `add_reaction`. Calling `bot.handle_webhook("slack", ...)` raises `AttributeError` on the first URL-verification attempt.
- **Discord, GitHub, Teams, Linear, Telegram, WhatsApp** have dispatch methods present (verified by grep). Teams ships 7 intentional `NotImplementedError` stubs (reactions + Graph reader); WhatsApp ships 2; Telegram ships 1. CHANGELOG.md still describes Telegram/WhatsApp as "placeholder stubs" — that's stale and is fixed in Phase 0.
- The `Adapter` symbol in `packages/chat/src/chat/types.py:643` is still `Any` — no structural Protocol enforces the dispatch surface, which is how the Slack/GChat gap reached a release candidate.

**End state.**
- Slack (webhook + Socket Mode) and GChat (HTTP webhook + Pub/Sub) carry a full Part-B port matching upstream `vercel/chat@4.26.0` semantics.
- The six already-dispatched adapters pass an E2E skeleton (`examples/e2e/<adapter>/echo.py`) plus a pinned test for each intentional `NotImplementedError`.
- `Adapter` in `types.py` is a real `Protocol`; `Chat.handle_webhook` is covered by a per-adapter integration test in `chat-integration-tests`.
- `docs/parity.md` has a "Dispatch surface" section; CHANGELOG.md `[Unreleased]` lists the DES-196 changes accurately.

**Scope decisions (already locked with Taras).**
- Commit granularity: **per feature/phase** (one commit when the phase's RED suite is fully green).
- Rollback: if a cycle can't reach GREEN, **keep the failing code and surface to Taras** — do not `git checkout`.
- Slack + GChat get full ports; the other six adapters get an **audit + E2E skeleton** phase (no re-port).
- `NotImplementedError` stubs are **pinned by tests** and documented in `parity.md`.
- Taras has real Slack workspace creds in `.env` and will manually run `examples/e2e/slack/echo.py` at the end.
- file-review opens on the finished plan for inline comments.

**TDD cycle format.**
Per repo-root CLAUDE.md planning rules, every cycle is `RED → GREEN → COMMIT (phase-end) | ROLLBACK (ask for guidance)`. Each phase ends with a **Verification** block (the exact unit + integration commands to run) and the upstream file it mirrors.

---

## Phase index

| # | Phase                                       | Cycles | Est. diff |
|---|---------------------------------------------|--------|-----------|
| 0 | Dispatch gap documentation + `Adapter` Protocol | 4  | ~200 LOC  |
| 1 | Slack webhook dispatch (Events API)         | 11     | ~2500 LOC |
| 2 | Slack Socket Mode dispatch                  | 5      | ~600 LOC  |
| 3 | GChat dispatch (HTTP + Pub/Sub)             | 8      | ~1500 LOC |
| 4 | Discord — audit + E2E skeleton              | 3      | ~150 LOC  |
| 5 | GitHub — audit + E2E skeleton               | 3      | ~150 LOC  |
| 6 | WhatsApp — audit + E2E skeleton + stubs     | 4      | ~180 LOC  |
| 7 | Teams — audit + E2E skeleton + stubs        | 4      | ~200 LOC  |
| 8 | Linear — audit + E2E skeleton               | 3      | ~150 LOC  |
| 9 | Telegram — audit + E2E skeleton + stubs     | 4      | ~180 LOC  |
| 10 | `Chat.handle_webhook` integration matrix   | 3      | ~250 LOC  |

---

## Phase 0 — Dispatch gap documentation + `Adapter` Protocol

**Goal.** Make the gap visible in docs and type-enforceable before shipping any implementation. This phase is cheap on its own but keeps the rest of the plan honest — every downstream phase asserts conformance against the Protocol landed here.

**Upstream mirror.** `packages/chat/src/types.ts` (Adapter interface), `vercel/chat` v4.26.0.

### QA Spec (optional)
- `mypy --strict` still passes on `packages/chat/src` after the Protocol lands.
- `grep "Adapter = Any" packages/chat/src/chat/types.py` returns zero matches.

### Cycle 0.1 — parity.md "Dispatch surface" section (RED)
**Test to write:** `packages/chat-integration-tests/tests/test_parity_doc.py`
```python
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PARITY = (REPO_ROOT / "docs" / "parity.md").read_text()

def test_parity_lists_dispatch_surface_per_adapter():
    assert "## Dispatch surface" in PARITY
    for adapter in ("slack", "gchat", "discord", "github", "teams", "linear", "telegram", "whatsapp"):
        assert adapter in PARITY.lower(), f"dispatch table missing {adapter}"

def test_parity_enumerates_intentional_not_implemented_stubs():
    assert "### Deliberate NotImplementedError stubs" in PARITY
    assert "chat-adapter-teams" in PARITY
    assert "chat-adapter-whatsapp" in PARITY
    assert "chat-adapter-telegram" in PARITY
```
**Expected failure:** `AssertionError: '## Dispatch surface' not found`.
**Verify RED:** `uv run pytest packages/chat-integration-tests/tests/test_parity_doc.py` — fails on first assert.

#### GREEN
- Edit `docs/parity.md`:
  - Add `## Dispatch surface` section with a table columned `adapter | handle_webhook | post | edit | delete | react | streaming | notes`. Row states = `full` / `stub` / `n/a (upstream limit)`.
  - Add `### Deliberate NotImplementedError stubs` listing: Teams reactions + `read_thread` + cert auth; WhatsApp the two stubs at `adapter.py:783,789`; Telegram the stub at `adapter.py:584`.
- Update CHANGELOG.md `[Unreleased]` with a `### Changed` line: "Adapter dispatch surface — Slack and GChat Part B ported; parity.md/CHANGELOG now reflect reality for Telegram/WhatsApp (previously labelled 'placeholder stub' — dispatch has been present since DES-182/DES-183)."

**Verify GREEN:** same pytest command passes.

### Cycle 0.2 — `Adapter` Protocol replaces `Any` (RED)
**Test to write:** `packages/chat/tests/test_adapter_protocol.py`
```python
import inspect
from typing import Protocol, runtime_checkable, get_type_hints
from chat.types import Adapter

def test_adapter_is_runtime_protocol():
    assert getattr(Adapter, "_is_protocol", False) is True, "Adapter must be a Protocol"

def test_adapter_declares_core_dispatch_surface():
    # These are the minimum methods every adapter must provide for Chat.handle_webhook to work.
    required = {
        "name", "initialize", "handle_webhook",
        "encode_thread_id", "decode_thread_id", "channel_id_from_thread_id",
        "post_message", "edit_message", "delete_message",
        "add_reaction", "remove_reaction",
    }
    present = {m for m in dir(Adapter) if not m.startswith("_")}
    missing = required - present
    assert not missing, f"Adapter Protocol missing: {missing}"
```
**Expected failure:** `AssertionError: Adapter must be a Protocol` (because today `Adapter = Any`).
**Verify RED:** `uv run pytest packages/chat/tests/test_adapter_protocol.py::test_adapter_is_runtime_protocol` — fails.

#### GREEN
- In `packages/chat/src/chat/types.py` replace `Adapter = Any` (line 643) with a `@runtime_checkable` `class Adapter(Protocol)` that declares: `name: str`; `lock_scope: LockScope | None`; `persist_message_history: bool`; plus async methods `initialize`, `disconnect`, `handle_webhook`, `post_message`, `edit_message`, `delete_message`, `add_reaction`, `remove_reaction`, `post_channel_message`, `fetch_messages`, `fetch_channel_info`, `fetch_channel_messages`, `list_threads`, `subscribe`, `unsubscribe`, `open_dm`, `open_modal`, `start_typing`, `stream`; plus sync methods `encode_thread_id`, `decode_thread_id`, `channel_id_from_thread_id`, `is_dm`, `get_channel_visibility`. Method bodies are `...`. Match upstream `types.ts` `Adapter` interface names 1:1.

**Verify GREEN:** pytest passes; `uv run mypy packages/chat/src` remains clean (Protocol is structural so existing call sites still type-check).

### Cycle 0.3 — Slack adapter fails conformance (RED)
**Test to write:** `packages/chat-adapter-slack/tests/test_protocol_conformance.py`
```python
from chat.types import Adapter
from chat_adapter_slack import create_slack_adapter

def test_slack_adapter_implements_adapter_protocol(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    adapter = create_slack_adapter()
    assert isinstance(adapter, Adapter), "SlackAdapter missing Protocol methods"
```
**Expected failure:** `AssertionError` — `SlackAdapter` lacks `handle_webhook`, `post_message`, etc.
**Verify RED:** `uv run pytest packages/chat-adapter-slack/tests/test_protocol_conformance.py` — fails.

#### GREEN (stays RED — intentional)
- This cycle ends RED and stays RED until Phase 1 lands. The failing test pins the gap so Phase 1 has a clear "done" signal.

### Cycle 0.4 — GChat conformance (RED)
**Test to write:** `packages/chat-adapter-gchat/tests/test_protocol_conformance.py` — same shape as 0.3 but for `create_google_chat_adapter`. Stays RED until Phase 3.

### Verification (Phase 0)
```bash
uv run ruff check packages/
uv run ruff format --check packages/
uv run mypy packages/chat/src
uv run pytest packages/chat packages/chat-integration-tests/tests/test_parity_doc.py
# Expect the two conformance tests (0.3, 0.4) to still be RED — that's the phase signal.
uv run pytest packages/chat-adapter-slack/tests/test_protocol_conformance.py packages/chat-adapter-gchat/tests/test_protocol_conformance.py || echo "expected red until Phase 1 / Phase 3"
```

**Commit:** `DES-196 phase 0: document dispatch surface + formalize Adapter Protocol`.
**Rollback:** surface to Taras — this phase is small, a failure likely indicates a circular-import issue in types.py and needs discussion.

---

## Phase 1 — Slack webhook dispatch (Events API + actions + modals)

**Goal.** Implement every `handle_webhook` branch + outbound method Slack needs for the echo E2E scenario and broader handler dispatch. Upstream structure dominates: URL verification, Events API (`app_mention`, `message`, `reaction_added/removed`, `assistant_thread_started`, `app_home_opened`, `member_joined_channel`), interactivity (`block_actions`, `view_submission`, `view_closed`), `slash_commands`, plus outbound `post_message` / `edit_message` / `delete_message` / `add_reaction` / streaming.

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-slack/src (entry: `index.ts` — ~4.7K lines). Key units: `handleWebhook`, `postMessage`, `editMessage`, `addReaction`, `openModal`, `streamingMessage`, `openDM`, `fetchMessages`.

**Test infrastructure.** Reuse `packages/chat-adapter-slack/tests/_builders.py` (existing fixture module). New file: `packages/chat-adapter-slack/tests/test_dispatch.py` for all Cycle-1.x tests. Use `pytest-asyncio` with `asyncio_mode = "auto"` (already configured). Mock `AsyncWebClient` via `unittest.mock.AsyncMock`. For Chat-level round-trips, use `create_mock_state()` from `chat.mock_adapter`.

### QA Spec (optional)
- Real URL-verification POST from `ngrok http 8000` → Slack Event Subscriptions returns `{challenge: ...}` with 200.
- Real `app_mention` event fires `on_new_mention` handler; `thread.post("hi")` lands in the Slack thread.
- `block_actions` from a Block Kit button triggers the registered `on_action` handler within 3s (Slack's ack timeout).

### Cycle 1.1 — URL verification handshake (RED)
**Test to write:** in `test_dispatch.py`
```python
import json, pytest, time, hmac, hashlib
from chat_adapter_slack import create_slack_adapter

SIGNING_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"

def _sign(body: str, ts: str) -> str:
    base = f"v0:{ts}:{body}".encode()
    return "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()

@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    return create_slack_adapter()

async def test_url_verification_returns_challenge(adapter):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"})
    ts = str(int(time.time()))
    status, _headers, resp = await adapter.handle_webhook(
        body.encode(),
        {
            "x-slack-request-timestamp": ts,
            "x-slack-signature": _sign(body, ts),
        },
    )
    assert status == 200
    assert json.loads(resp)["challenge"] == "abc123"
```
**Expected failure:** `AttributeError: 'SlackAdapter' object has no attribute 'handle_webhook'`.
**Verify RED:** `uv run pytest packages/chat-adapter-slack/tests/test_dispatch.py::test_url_verification_returns_challenge`.

#### GREEN
- Add `async def handle_webhook(self, body, headers, options=None) -> tuple[int, dict[str, str], bytes]` to `SlackAdapter`.
- Verify signature first (raise `AuthenticationError` on fail, returning `401`). On `url_verification` type, return `(200, {"content-type": "application/json"}, json.dumps({"challenge": payload["challenge"]}).encode())`.

**Verify GREEN:** pytest passes.

### Cycle 1.2 — Signature verification rejects tampered body (RED)
**Test to write:**
```python
async def test_signature_mismatch_returns_401(adapter):
    body = json.dumps({"type": "url_verification", "challenge": "x"})
    ts = str(int(time.time()))
    bad_sig = _sign(body + "-tampered", ts)
    status, _headers, _body = await adapter.handle_webhook(body.encode(), {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": bad_sig,
    })
    assert status == 401
```
**Expected failure:** either `challenge` is returned with 200 (no verify) or verify is tied to timestamp skew only.

#### GREEN
- Harden verify branch to return `(401, {}, b"")` when signature mismatch, timestamp missing, or skew > 300s.

### Cycle 1.3 — `app_mention` dispatches to `on_new_mention` via `Chat` (RED)
**Test to write:**
```python
import asyncio
from chat import Chat
from chat.mock_adapter import create_mock_state

async def test_app_mention_fires_mention_handler(adapter):
    bot = Chat(user_name="bot", adapters={"slack": adapter}, state=create_mock_state())
    seen = asyncio.Event()

    @bot.on_new_mention
    async def _h(thread, message):
        assert message.text == "<@U_BOT> hello"
        seen.set()

    body = json.dumps({
        "type": "event_callback",
        "event": {"type": "app_mention", "channel": "C1", "user": "U2",
                  "text": "<@U_BOT> hello", "ts": "1234.5", "thread_ts": "1234.5"},
    })
    ts = str(int(time.time()))
    await bot.handle_webhook("slack", body.encode(),
                             {"x-slack-request-timestamp": ts,
                              "x-slack-signature": _sign(body, ts)})
    await asyncio.wait_for(seen.wait(), timeout=2.0)
```
**Expected failure:** `AttributeError: ... 'handle_webhook'` → once 1.1 lands, failure shifts to "handler never called, timeout".

#### GREEN
- Inside `handle_webhook`, branch on `payload["type"] == "event_callback"`. For `event.type == "app_mention"`, build a `Message` (using existing format-converter + author lookup via `users.info` or cache), resolve `thread_id = encode_thread_id(...)`, and call `self._chat.process_message(self, thread_id, message)`. `self._chat` is set by `initialize(chat)` — also implement that method.

### Cycle 1.4 — `message` in subscribed thread → `on_subscribed_message` (RED)
Build a `Chat` with the thread pre-subscribed via `state.subscribe(thread_id)`. Post a `message` event. Assert `on_subscribed_message` fires and plain `on_new_message` does not. **Failure:** handler not called. **GREEN:** route non-app_mention `message` events through `process_message`; `Chat`'s subscription check handles the routing. Skip messages with `subtype` in (`bot_message` if `is_me`, `message_changed`, `message_deleted`) — delegate edits/deletes to a follow-up cycle.

### Cycle 1.5 — `reaction_added` / `reaction_removed` dispatch (RED)
Assert `on_reaction` handler receives a reaction event with emoji mapped to a `WellKnownEmoji` via `DEFAULT_EMOJI_MAP`, and `added=True/False`. **GREEN:** new branch calling `self._chat.process_reaction(event)`.

### Cycle 1.6 — `block_actions` interactivity dispatch (RED)
POST an `application/x-www-form-urlencoded` body with `payload=<json>` (upstream Slack interactivity shape). Assert `on_action` fires with `actionId`, `value`, `triggerId`. **GREEN:** add `x-www-form-urlencoded` body parser + `payload.type == "block_actions"` branch → `chat.process_action`.

### Cycle 1.7 — `view_submission` + `view_closed` (RED)
Trigger a modal submit with `callback_id="my-modal"` and assert a registered `on_modal_submit("my-modal")` handler fires. Response body must match Slack modal-close semantics (`{"response_action": "clear"}`). **GREEN:** modal branches wire through `chat.process_modal_submit` / `process_modal_close`.

### Cycle 1.8 — `slash_commands` dispatch (RED)
POST `command=/foo&text=bar&trigger_id=...`. Assert `on_slash_command("/foo")` fires and `open_modal(...)` helper works through the trigger ID. **GREEN:** `/commands` form parser + `chat.process_slash_command`.

### Cycle 1.9 — Outbound `post_message` (RED)
```python
async def test_post_message_calls_chat_post_message(adapter):
    adapter.client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "999.9", "channel": "C1"})
    result = await adapter.post_message("slack:C1:888.8", {"markdown": "hi"})
    adapter.client.chat_postMessage.assert_awaited_once()
    assert result["id"] == "999.9"
```
**GREEN:** implement `post_message(thread_id, message) -> RawMessage` — decode `thread_id`, convert `PostableMarkdown`/`PostableAst` via `self.format_converter` to Block Kit, call `chat_postMessage`, return `{id, raw, threadId}`.

### Cycle 1.10 — Outbound `edit_message`, `delete_message`, `add_reaction`, `remove_reaction` (RED)
Four sub-tests that mock the corresponding `chat_update`, `chat_delete`, `reactions_add`, `reactions_remove` calls. **GREEN:** four thin wrappers that call the Slack Web API method and translate `RateLimitError` / `AuthenticationError` from `slack_sdk.errors.SlackApiError`.

### Cycle 1.11 — Streaming (`thread.post(async_iter)`) (RED)
Test feeds an `AsyncIterable[str]` chunks; assert `chat_update` is called every 500ms (configurable) with the accumulated text, and once more at stream close. **GREEN:** `stream(thread_id, chunks, options) -> RawMessage` using `asyncio.sleep(streaming_update_interval_ms/1000)` throttle. Mirrors upstream `StreamingMarkdownRenderer` in `adapter-slack/src/streaming.ts`.

### Verification (Phase 1)
```bash
uv run ruff check packages/chat-adapter-slack packages/chat
uv run ruff format --check packages/chat-adapter-slack packages/chat
uv run mypy packages/chat/src
uv run pytest packages/chat-adapter-slack -x
uv run pytest packages/chat-adapter-slack/tests/test_protocol_conformance.py  # now GREEN
uv run pytest packages/chat-integration-tests/tests/test_dispatch_memory.py -k slack
```

**Commit:** `DES-196 phase 1: Slack webhook dispatch (Events API + interactivity + outbound)`.
**Rollback:** if any cycle can't reach GREEN, pause and surface to Taras — do not revert.

---

## Phase 2 — Slack Socket Mode dispatch

**Goal.** Make `mode="socket"` work end-to-end: `connect()` opens the websocket, `disconnect()` closes it, incoming `events_api` / `interactive` / `slash_commands` envelopes are ack'd and routed through the same internal dispatch as Phase 1.

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-slack/src — the `SocketModeClient` branch of `index.ts`.

**Runtime.** `slack_sdk.socket_mode.aiohttp.SocketModeClient`. Keep the `handle_webhook` public surface unchanged; socket mode just bypasses the HTTP entry by feeding envelopes to a shared `_dispatch_envelope(payload)` helper extracted in Phase 1, Cycle 1.3.

### Cycle 2.1 — `connect()` opens a socket when `mode="socket"` (RED)
Mock `SocketModeClient.connect_async`. `adapter.connect()` should await it. **GREEN:** add `async def connect(self) -> None` + `async def disconnect(self) -> None`. On `connect`, install `socket_mode_request_listeners` that call `_dispatch_envelope`.

### Cycle 2.2 — `events_api` envelope routed to handler (RED)
Feed a synthesized `SocketModeRequest(type="events_api", payload={...app_mention...})`. Assert `on_new_mention` fires. **GREEN:** listener translates the envelope into the same payload Phase 1 handles, then calls `_dispatch_envelope`.

### Cycle 2.3 — Every envelope is ack'd within 3s (RED)
Assert the mocked `client.send_socket_mode_response` is called with `envelope_id` before handler dispatch returns. **GREEN:** ack immediately, then dispatch in a `asyncio.create_task` — matches upstream order of operations.

### Cycle 2.4 — `disconnect()` closes the socket (RED)
Assert `SocketModeClient.close_async` is awaited. **GREEN:** simple wrapper.

### Cycle 2.5 — Chat-level init wires Socket Mode (RED)
```python
async def test_chat_initialize_connects_socket_mode(monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    adapter = create_slack_adapter({"mode": "socket", "botToken": "xoxb", "signingSecret": None})
    adapter.connect = AsyncMock()
    bot = Chat(user_name="bot", adapters={"slack": adapter}, state=create_mock_state())
    await bot.initialize()
    adapter.connect.assert_awaited_once()
```
**GREEN:** `Adapter.initialize(chat)` (in `SlackAdapter`) calls `self.connect()` when `self.is_socket_mode is True`.

### Verification (Phase 2)
```bash
uv run pytest packages/chat-adapter-slack/tests/test_dispatch.py -k socket
uv run pytest packages/chat-adapter-slack -x
```

**Commit:** `DES-196 phase 2: Slack Socket Mode dispatch`.
**Rollback:** ask Taras.

---

## Phase 3 — GChat dispatch (HTTP webhook + Pub/Sub)

**Goal.** GChat currently only exposes `verify_bearer_token` / `verify_pubsub_bearer` / space-subscription helpers. Port the `handle_webhook` + outbound surface. Google Chat uses two receive paths depending on app setup: HTTP endpoint (`POST /api/webhooks/gchat`) and Pub/Sub push (`POST /...pubsub/push`). Both must funnel into the same dispatch.

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-gchat/src — `index.ts` plus `pubsub.ts`.

**Test infra.** New file `packages/chat-adapter-gchat/tests/test_dispatch.py`. Use `pytest-asyncio`; mock the Chat REST client (Google API lib) via `AsyncMock`. Build a canned JWT for `verify_bearer_token` via `python-jose` or by monkeypatching the verifier.

### Cycle 3.1 — Conformance test flips GREEN (RED → GREEN)
Already-RED test from Phase 0 Cycle 0.4. Becomes GREEN once `handle_webhook` + outbound methods are declared on `GoogleChatAdapter`.

### Cycle 3.2 — `MESSAGE` event on HTTP path dispatches `on_new_mention`
Signed JWT bearer → valid. Payload `type="MESSAGE"`, `message.argumentText=" hello"` (leading-space = mention semantics in GChat). Assert `on_new_mention` fires.

### Cycle 3.3 — `ADDED_TO_SPACE` / `REMOVED_FROM_SPACE`
Assert subscription helpers are invoked on `ADDED_TO_SPACE`.

### Cycle 3.4 — Pub/Sub envelope routes same payload
Pub/Sub wrapper: `{"message": {"data": base64(event_json), "attributes": {"ce-type": "google.workspace.chat.message.v1.created"}}}`. Assert same handler fires. **GREEN:** `handle_webhook` checks `headers["content-type"]` for `application/json`, tries Pub/Sub shape first, then HTTP event shape.

### Cycle 3.5 — `post_message` posts a Card v2 via REST
Mock the Chat REST client `spaces.messages.create`. Assert the card converter ran and the resulting payload matches the upstream snapshot (compare to `packages/chat-adapter-gchat/tests/__fixtures__/card_v2_snapshot.json`, carry over from upstream test data).

### Cycle 3.6 — `edit_message`, `delete_message`
Mock `spaces.messages.update` / `.delete`. Upstream edit uses `update_mask=text,cards_v2`.

### Cycle 3.7 — `add_reaction` / `remove_reaction`
GChat uses `messages.reactions.create` / `.delete`. Map `WellKnownEmoji` → Unicode via existing `chat.emoji.DEFAULT_EMOJI_MAP`.

### Cycle 3.8 — `fetch_messages` pagination
Mock `spaces.messages.list` returning `{messages, nextPageToken}`. Assert pagination honors `FetchOptions.cursor`.

### Verification (Phase 3)
```bash
uv run ruff check packages/chat-adapter-gchat
uv run mypy packages/chat/src
uv run pytest packages/chat-adapter-gchat -x
uv run pytest packages/chat-adapter-gchat/tests/test_protocol_conformance.py  # now GREEN
```

**Commit:** `DES-196 phase 3: GChat dispatch (HTTP webhook + Pub/Sub + outbound)`.

---

## Phase 4 — Discord audit + E2E skeleton

**Goal.** Lock the existing Discord dispatch against the new `Adapter` Protocol, pin behavior with a Chat-level integration test, and create the E2E skeleton.

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-discord/src

### Cycle 4.1 — Protocol conformance (RED → GREEN via fixups)
**Test to write:** `packages/chat-adapter-discord/tests/test_protocol_conformance.py` — `isinstance(adapter, Adapter)` on a zero-config adapter. **Expected failure (if any):** whatever Protocol methods Discord is actually missing — e.g. `open_modal` for Discord (Discord has no modals per se; may need to raise `chat.NotImplementedError` to satisfy Protocol). **GREEN:** add any stubs that raise `chat.NotImplementedError("discord", feature="modals")`, document in `parity.md`.

### Cycle 4.2 — `Chat.handle_webhook("discord", ...)` round-trip (RED)
Build a realistic `INTERACTION_CREATE` ed25519-signed payload; route via `Chat.handle_webhook`. Assert the registered `on_new_mention` / `on_slash_command` fires. **GREEN:** if test fails, it means Discord's `handle_webhook` mis-wires `self._chat` — fix the init path.

### Cycle 4.3 — E2E skeleton script
Create `examples/e2e/discord/echo.py` mirroring `examples/e2e/slack/echo.py` shape. Docstring lists `DISCORD_PUBLIC_KEY`, `DISCORD_BOT_TOKEN` env vars, and Discord developer-portal steps (register app, add `applications.commands` scope, set Interactions Endpoint URL).

### Verification (Phase 4)
```bash
uv run pytest packages/chat-adapter-discord -x
uv run python -c "import ast; ast.parse(open('examples/e2e/discord/echo.py').read())"
```

**Commit:** `DES-196 phase 4: Discord protocol conformance + E2E skeleton`.

---

## Phase 5 — GitHub audit + E2E skeleton

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-github/src

### Cycle 5.1 — Protocol conformance test (RED → GREEN)
Same shape as 4.1. GitHub doesn't natively support `add_reaction` for issue comments (only 👍/👎/etc via reactions API) — make sure the stubs that are real and the ones that are intentional are distinguished.

### Cycle 5.2 — `issue_comment.created` round-trip via `Chat.handle_webhook`
Build HMAC-SHA256-signed webhook body, headers include `X-Hub-Signature-256`, `X-GitHub-Event: issue_comment`, `X-GitHub-Delivery: <uuid>`. Assert `on_new_mention` fires when the issue body contains `@<bot-user-name>`.

### Cycle 5.3 — E2E skeleton script
Create `examples/e2e/github/echo.py`. Env vars: `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_BOT_USERNAME`. Docstring covers GitHub-App creation, installation, webhook URL config.

### Verification (Phase 5)
```bash
uv run pytest packages/chat-adapter-github -x
uv run python -c "import ast; ast.parse(open('examples/e2e/github/echo.py').read())"
```

**Commit:** `DES-196 phase 5: GitHub protocol conformance + E2E skeleton`.

---

## Phase 6 — WhatsApp audit + E2E skeleton + stub pinning

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-whatsapp/src

### Cycle 6.1 — Protocol conformance test
Same shape.

### Cycle 6.2 — `messages` webhook round-trip via `Chat.handle_webhook`
Cloud API webhook shape: `{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"messages":[{...}]}}]}]}`. Header `X-Hub-Signature-256: sha256=<hex>` (HMAC over body). Assert `on_direct_message` handler fires (WhatsApp is DM-only).

### Cycle 6.3 — Pin `NotImplementedError` stubs
Find the two `NotImplementedError` sites at `adapter.py:783,789` — write tests that call those methods and assert `chat.NotImplementedError` (not `builtins.NotImplementedError`). Add both to `parity.md` → `### Deliberate NotImplementedError stubs`.

### Cycle 6.4 — E2E skeleton
Create `examples/e2e/whatsapp/echo.py`. Env vars: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_SECRET`, `WHATSAPP_VERIFY_TOKEN`. Docstring: Meta for Developers → Business App → Webhooks → subscribe `messages`. Note the verify-token handshake (GET `hub.challenge`).

### Verification (Phase 6)
```bash
uv run pytest packages/chat-adapter-whatsapp -x
uv run python -c "import ast; ast.parse(open('examples/e2e/whatsapp/echo.py').read())"
```

**Commit:** `DES-196 phase 6: WhatsApp protocol conformance + stub pinning + E2E skeleton`.

---

## Phase 7 — Teams audit + E2E skeleton + stub pinning

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-teams/src

### Cycle 7.1 — Protocol conformance (RED → GREEN via `chat.NotImplementedError` adjustments)
Teams already raises `chat.NotImplementedError` for reactions + `read_thread` + cert auth (7 sites — `adapter.py:444-495`). Ensure all of those flow through `chat.errors.NotImplementedError` (not builtin) and that `Adapter` Protocol conformance still holds.

### Cycle 7.2 — `message` activity round-trip via `Chat.handle_webhook`
Bot Framework activity shape + JWT bearer header. Use a canned JWT from `packages/chat-adapter-teams/tests/__fixtures__/` if available, else monkeypatch `verify_jwt`. Assert `on_new_mention` fires.

### Cycle 7.3 — Pin the 7 `NotImplementedError` stubs
`test_unsupported_features.py`: call each and assert `chat.NotImplementedError` with the right `feature=` attribute. Add to `parity.md`.

### Cycle 7.4 — E2E skeleton
Create `examples/e2e/teams/echo.py`. Env vars: `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, `TEAMS_TENANT_ID`. Docstring covers Azure App Registration, messaging endpoint, ngrok.

### Verification (Phase 7)
```bash
uv run pytest packages/chat-adapter-teams -x
uv run python -c "import ast; ast.parse(open('examples/e2e/teams/echo.py').read())"
```

**Commit:** `DES-196 phase 7: Teams protocol conformance + stub pinning + E2E skeleton`.

---

## Phase 8 — Linear audit + E2E skeleton

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-linear/src

### Cycle 8.1 — Protocol conformance
Same shape. Linear's `add_reaction` / `remove_reaction` currently exist as no-ops or `NotImplementedError` — audit and document.

### Cycle 8.2 — `Comment` webhook round-trip via `Chat.handle_webhook`
Linear webhook header `Linear-Signature` + HMAC. Assert `on_new_mention` fires when a comment mentions the bot (via Linear's `@mention` token).

### Cycle 8.3 — E2E skeleton
Create `examples/e2e/linear/echo.py`. Env vars: `LINEAR_API_KEY`, `LINEAR_WEBHOOK_SECRET`.

### Verification (Phase 8)
```bash
uv run pytest packages/chat-adapter-linear -x
uv run python -c "import ast; ast.parse(open('examples/e2e/linear/echo.py').read())"
```

**Commit:** `DES-196 phase 8: Linear protocol conformance + E2E skeleton`.

---

## Phase 9 — Telegram audit + E2E skeleton + stub pinning

**Upstream mirror.** https://github.com/vercel/chat/tree/main/packages/adapter-telegram/src

### Cycle 9.1 — Protocol conformance test

### Cycle 9.2 — `message` update round-trip via `Chat.handle_webhook`
Telegram webhook body shape: `{"update_id": ..., "message": {...}}`. No signature verify (Telegram uses secret token in URL instead). Assert `on_new_mention` or `on_direct_message` fires.

### Cycle 9.3 — Pin the `NotImplementedError` stub
`adapter.py:584` — write a test pinning the behavior + `parity.md` entry.

### Cycle 9.4 — E2E skeleton
Create `examples/e2e/telegram/echo.py`. Env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`.

### Verification (Phase 9)
```bash
uv run pytest packages/chat-adapter-telegram -x
uv run python -c "import ast; ast.parse(open('examples/e2e/telegram/echo.py').read())"
```

**Commit:** `DES-196 phase 9: Telegram protocol conformance + stub pinning + E2E skeleton`.

---

## Phase 10 — `Chat.handle_webhook` integration matrix

**Goal.** One test per adapter that routes a canned webhook body through `Chat.handle_webhook(<name>, body, headers)` and asserts the expected handler fires — a single regression-catcher that the next adapter refactor will have to respect.

**File.** Extend `packages/chat-integration-tests/tests/test_dispatch_memory.py` (existing — per-state-backend dispatch tests).

### Cycle 10.1 — Parametrized "every adapter routes `Chat.handle_webhook`" test (RED)
```python
import pytest

@pytest.mark.parametrize("adapter_name,body,headers,expected_handler", [
    ("slack",    SLACK_APP_MENTION_BODY,    SLACK_HEADERS,    "on_new_mention"),
    ("gchat",    GCHAT_MESSAGE_BODY,        GCHAT_HEADERS,    "on_new_mention"),
    ("discord",  DISCORD_INTERACTION_BODY,  DISCORD_HEADERS,  "on_slash_command"),
    ("github",   GITHUB_ISSUE_COMMENT_BODY, GITHUB_HEADERS,   "on_new_mention"),
    ("whatsapp", WHATSAPP_MESSAGE_BODY,     WHATSAPP_HEADERS, "on_direct_message"),
    ("teams",    TEAMS_MESSAGE_BODY,        TEAMS_HEADERS,    "on_new_mention"),
    ("linear",   LINEAR_COMMENT_BODY,       LINEAR_HEADERS,   "on_new_mention"),
    ("telegram", TELEGRAM_MESSAGE_BODY,     TELEGRAM_HEADERS, "on_direct_message"),
])
async def test_chat_routes_webhook_to_handler(adapter_name, body, headers, expected_handler):
    bot = _build_bot_for(adapter_name)  # factory registers one of each handler
    await bot.handle_webhook(adapter_name, body, headers)
    assert _handler_fired(bot, expected_handler)
```
**Expected failure:** any adapter that mis-wires `self._chat` or returns wrong payload shape.

### Cycle 10.2 — Fixture bodies live in `tests/__fixtures__/`
Each fixture is a real-world webhook capture (scrubbed of secrets). Document provenance in a comment at top of each file.

### Cycle 10.3 — Parity-doc self-test
Assert every row in `parity.md`'s "Dispatch surface" table corresponds to an actual adapter module (walk the `packages/` tree, cross-reference).

### Verification (Phase 10)
```bash
uv run pytest packages/chat-integration-tests -x
uv run ruff check packages/
uv run mypy packages/chat/src
```

**Commit:** `DES-196 phase 10: Chat.handle_webhook integration matrix + parity self-test`.

---

## Final verification (whole plan)

```bash
uv sync --all-packages --dev
uv run ruff check packages/
uv run ruff format --check packages/
uv run mypy packages/chat/src
uv run pytest packages/ -x
```

All four must pass before v0.1.0 publish is unblocked.

---

## Manual E2E

Run one terminal per provider. Taras has Slack creds in `.env` — that scenario is the gating acceptance test; the others ship as skeletons that compile and import cleanly but require provider setup (credentials + endpoint registration) to exercise.

**Slack (gating — Taras runs for sign-off).**
```bash
uv sync --group e2e
# terminal 1
uv run python examples/e2e/slack/echo.py
# terminal 2
ngrok http 8000
# paste https://…ngrok.app/api/webhooks/slack into Slack Event Subscriptions → Request URL
# Slack UI: @-mention the bot in a channel; bot replies and subscribes.
# Reply again in the thread; bot echoes.
```

**Slack Socket Mode (optional — same creds, no ngrok).**
```bash
SLACK_APP_TOKEN=xapp-... uv run python examples/e2e/slack/echo.py --mode socket
```
(Add a `--mode` flag to `echo.py` in Phase 2 Cycle 2.5.)

**GChat (Phase 3).**
```bash
uv run python examples/e2e/gchat/echo.py
# Google Cloud console: create a Chat app, point HTTP endpoint at ngrok URL, add app to a space, @-mention.
```
(Create skeleton at the end of Phase 3 — it's a new E2E scenario; the subtask is part of that phase.)

**Discord / GitHub / WhatsApp / Teams / Linear / Telegram (Phase 4–9 skeletons).**
```bash
uv run python examples/e2e/discord/echo.py
uv run python examples/e2e/github/echo.py
uv run python examples/e2e/whatsapp/echo.py
uv run python examples/e2e/teams/echo.py
uv run python examples/e2e/linear/echo.py
uv run python examples/e2e/telegram/echo.py
```
Each exits with a clear "missing env vars" message when creds aren't set. Provider-specific setup is documented in the script docstring. None of these are gating for the v0.1.0 publish — they exist to unblock per-adapter live validation later.

---

## Out of scope

- Rebranding / relicensing / package renames (done in DES-195).
- Real PyPI publish (gated by v0.1.0 / v0.2.0 release decision; not a planning concern).
- Per-adapter deep test-parity audit against upstream test files (would push scope to weeks of TDD cycles; Taras ruled it out).
- Re-implementing dispatch for the six adapters that already have it — unless a conformance test surfaces a real bug.

## Success criteria (end state)

- [x] `docs/parity.md` has "Dispatch surface" table + "Deliberate NotImplementedError stubs" subsection.
- [x] CHANGELOG.md `[Unreleased]` accurately describes DES-196 changes; outdated Telegram/WhatsApp "placeholder" wording corrected.
- [x] `chat.types.Adapter` is a `@runtime_checkable` `Protocol` (no more `Any`).
- [ ] `isinstance(create_*_adapter(), Adapter)` returns `True` for all 8 adapters.
- [ ] `Chat.handle_webhook("slack", ...)` works end-to-end against the real Slack Events API (Taras manual E2E).
- [ ] `Chat.handle_webhook("gchat", ...)` works end-to-end against a Pub/Sub push or HTTP webhook (integration tested, not live).
- [ ] Skeletons for `examples/e2e/{discord,github,whatsapp,teams,linear,telegram,gchat}/echo.py` exist and `ast.parse` cleanly.
- [ ] Every intentional `NotImplementedError` stub (Teams 7, WhatsApp 2, Telegram 1) has a pinned test + `parity.md` entry.
- [ ] Full validation suite green: `ruff check`, `ruff format --check`, `mypy packages/chat/src`, `pytest packages/`.
