"""Phase 1 dispatch tests for :class:`SlackAdapter`.

Mirrors upstream ``packages/adapter-slack/src/index.ts`` dispatch branches
(URL verification, Events API, interactivity, slash commands, streaming,
outbound operations).

All tests use mocked ``AsyncWebClient`` methods and
:func:`chat.mock_adapter.create_mock_state` for Chat-level round-trips.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_slack import create_slack_adapter
from chat_adapter_slack.adapter import SlackAdapter

SIGNING_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


def _sign(body: str, ts: str, secret: str = SIGNING_SECRET) -> str:
    base = f"v0:{ts}:{body}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _headers(body: str, content_type: str = "application/json") -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": _sign(body, ts),
        "content-type": content_type,
    }


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> SlackAdapter:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    adp = create_slack_adapter()
    adp._bot_user_id = "U_BOT"
    return adp


# ---------------------------------------------------------------------------
# Cycle 1.1 — URL verification handshake
# ---------------------------------------------------------------------------


async def test_url_verification_returns_challenge(adapter: SlackAdapter) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "abc123"})
    status, headers, resp = await adapter.handle_webhook(body.encode(), _headers(body))
    assert status == 200
    assert headers.get("content-type") == "application/json"
    assert json.loads(resp)["challenge"] == "abc123"


# ---------------------------------------------------------------------------
# Cycle 1.2 — Signature verification rejects tampered body
# ---------------------------------------------------------------------------


async def test_signature_mismatch_returns_401(adapter: SlackAdapter) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"})
    ts = str(int(time.time()))
    bad_sig = _sign(body + "-tampered", ts)
    status, _h, _b = await adapter.handle_webhook(
        body.encode(),
        {
            "x-slack-request-timestamp": ts,
            "x-slack-signature": bad_sig,
            "content-type": "application/json",
        },
    )
    assert status == 401


async def test_missing_timestamp_returns_401(adapter: SlackAdapter) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"})
    status, _h, _b = await adapter.handle_webhook(
        body.encode(),
        {"x-slack-signature": "v0=whatever", "content-type": "application/json"},
    )
    assert status == 401


async def test_stale_timestamp_returns_401(adapter: SlackAdapter) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"})
    stale_ts = str(int(time.time()) - 600)  # 10 minutes ago
    sig = _sign(body, stale_ts)
    status, _h, _b = await adapter.handle_webhook(
        body.encode(),
        {
            "x-slack-request-timestamp": stale_ts,
            "x-slack-signature": sig,
            "content-type": "application/json",
        },
    )
    assert status == 401


# ---------------------------------------------------------------------------
# Cycle 1.3 — app_mention dispatches to on_new_mention
# ---------------------------------------------------------------------------


def _make_bot(adapter: SlackAdapter) -> Chat:
    return Chat(user_name="bot", adapters={"slack": adapter}, state=create_mock_state())


async def test_app_mention_fires_mention_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, context: Any = None) -> None:
        captured["text"] = message.text
        captured["thread_id"] = thread.id
        seen.set()

    bot.on_new_mention(handler)

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U2",
                "text": "<@U_BOT> hello",
                "ts": "1234.5",
                "thread_ts": "1234.5",
            },
        }
    )
    status, _h, _b = await bot.handle_webhook("slack", body.encode(), _headers(body))
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "<@U_BOT> hello"
    assert captured["thread_id"] == "slack:C1:1234.5"


async def test_initialize_stores_chat_reference(adapter: SlackAdapter) -> None:
    sentinel = object()
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel


# ---------------------------------------------------------------------------
# Cycle 1.4 — message in subscribed thread → on_subscribed_message
# ---------------------------------------------------------------------------


async def test_subscribed_message_fires_subscribed_handler(adapter: SlackAdapter) -> None:
    state = create_mock_state()
    bot = Chat(user_name="bot", adapters={"slack": adapter}, state=state)
    await state.subscribe("slack:C1:1234.5")

    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def subscribed_handler(thread: Any, message: Any, context: Any = None) -> None:
        captured["text"] = message.text
        seen.set()

    mention_fired = asyncio.Event()

    async def mention_handler(thread: Any, message: Any, context: Any = None) -> None:
        mention_fired.set()

    bot.on_subscribed_message(subscribed_handler)
    bot.on_new_mention(mention_handler)

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C1",
                "user": "U2",
                "text": "hello there",
                "ts": "9999.5",
                "thread_ts": "1234.5",
            },
        }
    )
    status, _h, _b = await bot.handle_webhook("slack", body.encode(), _headers(body))
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "hello there"
    assert not mention_fired.is_set()


async def test_message_changed_is_skipped(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    fired = asyncio.Event()

    async def handler(thread: Any, message: Any, context: Any = None) -> None:
        fired.set()

    bot.on_new_mention(handler)

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "channel": "C1",
                "user": "U2",
                "text": "<@U_BOT> edited",
                "ts": "1.1",
                "thread_ts": "1.1",
            },
        }
    )
    await bot.handle_webhook("slack", body.encode(), _headers(body))
    # Give any fire-and-forget tasks a chance to run.
    await asyncio.sleep(0.05)
    assert not fired.is_set()


# ---------------------------------------------------------------------------
# Cycle 1.5 — reaction_added / reaction_removed dispatch
# ---------------------------------------------------------------------------


async def test_reaction_added_fires_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(event: dict[str, Any]) -> None:
        captured.update(event)
        seen.set()

    bot.on_reaction(handler)

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "reaction_added",
                "user": "U2",
                "reaction": "thumbsup",
                "item": {"type": "message", "channel": "C1", "ts": "123.456"},
                "item_user": "U_BOT",
                "event_ts": "1.2",
            },
        }
    )
    await bot.handle_webhook("slack", body.encode(), _headers(body))
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["added"] is True
    # Normalized emoji — "thumbsup" maps to "thumbs_up".
    assert captured["emoji"].name == "thumbs_up"
    assert captured["rawEmoji"] == "thumbsup"


async def test_reaction_removed_fires_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(event: dict[str, Any]) -> None:
        captured.update(event)
        seen.set()

    bot.on_reaction(handler)

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "reaction_removed",
                "user": "U2",
                "reaction": "heart",
                "item": {"type": "message", "channel": "C1", "ts": "123.456"},
                "event_ts": "1.2",
            },
        }
    )
    await bot.handle_webhook("slack", body.encode(), _headers(body))
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["added"] is False


# ---------------------------------------------------------------------------
# Cycle 1.6 — block_actions interactivity dispatch
# ---------------------------------------------------------------------------


async def test_block_actions_fires_action_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(event: dict[str, Any]) -> None:
        captured.update(event)
        seen.set()

    bot.on_action("my-btn", handler)

    payload = {
        "type": "block_actions",
        "user": {"id": "U2", "name": "alice", "username": "alice"},
        "channel": {"id": "C1"},
        "message": {"ts": "123.456", "thread_ts": "123.456"},
        "trigger_id": "trig-1",
        "actions": [
            {
                "action_id": "my-btn",
                "value": "clicked",
                "type": "button",
            }
        ],
    }
    body = f"payload={json.dumps(payload)}"
    status, _h, _b = await bot.handle_webhook(
        "slack", body.encode(), _headers(body, "application/x-www-form-urlencoded")
    )
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["actionId"] == "my-btn"
    assert captured["value"] == "clicked"
    assert captured["triggerId"] == "trig-1"


# ---------------------------------------------------------------------------
# Cycle 1.7 — view_submission + view_closed
# ---------------------------------------------------------------------------


async def test_view_submission_fires_modal_submit_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    captured: dict[str, Any] = {}

    async def handler(event: dict[str, Any]) -> None:
        captured.update(event)

    bot.on_modal_submit("my-modal", handler)

    payload = {
        "type": "view_submission",
        "user": {"id": "U2", "name": "alice", "username": "alice"},
        "view": {
            "id": "V1",
            "callback_id": "my-modal",
            "state": {
                "values": {
                    "block1": {
                        "input-name": {
                            "type": "plain_text_input",
                            "value": "hello",
                        }
                    }
                }
            },
        },
    }
    body = f"payload={json.dumps(payload)}"
    status, headers, resp = await bot.handle_webhook(
        "slack", body.encode(), _headers(body, "application/x-www-form-urlencoded")
    )
    assert status == 200
    assert headers.get("content-type") == "application/json"
    assert json.loads(resp) == {"response_action": "clear"}
    assert captured["callbackId"] == "my-modal"
    assert captured["values"] == {"input-name": "hello"}


async def test_view_closed_fires_modal_close_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()

    async def handler(event: dict[str, Any]) -> None:
        seen.set()

    bot.on_modal_close("my-modal", handler)

    payload = {
        "type": "view_closed",
        "user": {"id": "U2", "name": "alice"},
        "view": {"id": "V1", "callback_id": "my-modal"},
    }
    body = f"payload={json.dumps(payload)}"
    status, _h, _b = await bot.handle_webhook(
        "slack", body.encode(), _headers(body, "application/x-www-form-urlencoded")
    )
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)


# ---------------------------------------------------------------------------
# Cycle 1.8 — slash_commands dispatch
# ---------------------------------------------------------------------------


async def test_slash_command_fires_handler(adapter: SlackAdapter) -> None:
    bot = _make_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(event: dict[str, Any]) -> None:
        captured["command"] = event["command"]
        captured["text"] = event.get("text")
        captured["triggerId"] = event.get("triggerId")
        seen.set()

    bot.on_slash_command("/foo", handler)

    form_body = "command=/foo&text=bar&trigger_id=trig-2&user_id=U2&user_name=alice&channel_id=C1"
    status, _h, _b = await bot.handle_webhook(
        "slack", form_body.encode(), _headers(form_body, "application/x-www-form-urlencoded")
    )
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["command"] == "/foo"
    assert captured["text"] == "bar"
    assert captured["triggerId"] == "trig-2"


# ---------------------------------------------------------------------------
# Cycle 1.9 — Outbound post_message
# ---------------------------------------------------------------------------


async def test_post_message_calls_chat_post_message(adapter: SlackAdapter) -> None:
    adapter.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )
    result = await adapter.post_message("slack:C1:888.8", {"markdown": "hi"})
    adapter.client.chat_postMessage.assert_awaited_once()
    call = adapter.client.chat_postMessage.await_args
    assert call.kwargs["channel"] == "C1"
    assert call.kwargs["thread_ts"] == "888.8"
    assert "hi" in call.kwargs["text"]
    assert result["id"] == "999.9"
    assert result["threadId"] == "slack:C1:888.8"


async def test_post_message_handles_plain_string(adapter: SlackAdapter) -> None:
    adapter.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )
    result = await adapter.post_message("slack:C1:", "hello world")
    call = adapter.client.chat_postMessage.await_args
    assert "hello world" in call.kwargs["text"]
    assert result["id"] == "999.9"


# ---------------------------------------------------------------------------
# Cycle 1.10 — edit_message, delete_message, add_reaction, remove_reaction
# ---------------------------------------------------------------------------


async def test_edit_message_calls_chat_update(adapter: SlackAdapter) -> None:
    adapter.client.chat_update = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )
    result = await adapter.edit_message("slack:C1:888.8", "999.9", {"markdown": "edited"})
    adapter.client.chat_update.assert_awaited_once()
    call = adapter.client.chat_update.await_args
    assert call.kwargs["channel"] == "C1"
    assert call.kwargs["ts"] == "999.9"
    assert result["id"] == "999.9"


async def test_delete_message_calls_chat_delete(adapter: SlackAdapter) -> None:
    adapter.client.chat_delete = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
    await adapter.delete_message("slack:C1:888.8", "999.9")
    adapter.client.chat_delete.assert_awaited_once_with(channel="C1", ts="999.9")


async def test_add_reaction_calls_reactions_add(adapter: SlackAdapter) -> None:
    adapter.client.reactions_add = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True}
    )
    await adapter.add_reaction("slack:C1:888.8", "999.9", "thumbs_up")
    adapter.client.reactions_add.assert_awaited_once()
    call = adapter.client.reactions_add.await_args
    assert call.kwargs["channel"] == "C1"
    assert call.kwargs["timestamp"] == "999.9"
    assert call.kwargs["name"] in ("+1", "thumbsup", "thumbs_up")


async def test_remove_reaction_calls_reactions_remove(adapter: SlackAdapter) -> None:
    adapter.client.reactions_remove = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True}
    )
    await adapter.remove_reaction("slack:C1:888.8", "999.9", "thumbs_up")
    adapter.client.reactions_remove.assert_awaited_once()


async def test_slack_api_error_translates_rate_limit(adapter: SlackAdapter) -> None:
    from chat.errors import RateLimitError
    from slack_sdk.errors import SlackApiError

    err = SlackApiError(
        "rate limited",
        response={"ok": False, "error": "ratelimited", "headers": {"Retry-After": "30"}},
    )
    adapter.client.chat_postMessage = AsyncMock(side_effect=err)  # type: ignore[method-assign]

    with pytest.raises(RateLimitError) as exc_info:
        await adapter.post_message("slack:C1:888.8", "hi")
    assert exc_info.value.retry_after_ms == 30_000


async def test_slack_api_error_translates_auth(adapter: SlackAdapter) -> None:
    from chat_adapter_shared import AuthenticationError
    from slack_sdk.errors import SlackApiError

    err = SlackApiError(
        "auth failed",
        response={"ok": False, "error": "invalid_auth"},
    )
    adapter.client.chat_postMessage = AsyncMock(side_effect=err)  # type: ignore[method-assign]

    with pytest.raises(AuthenticationError):
        await adapter.post_message("slack:C1:888.8", "hi")


# ---------------------------------------------------------------------------
# Cycle 1.11 — Streaming
# ---------------------------------------------------------------------------


async def test_stream_posts_placeholder_then_updates(adapter: SlackAdapter) -> None:
    adapter.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )
    adapter.client.chat_update = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )

    async def chunks() -> Any:
        yield "Hello "
        await asyncio.sleep(0.01)
        yield "world"

    result = await adapter.stream(
        "slack:C1:888.8", chunks(), options={"streamingUpdateIntervalMs": 10}
    )
    # Initial post + at least one update on close.
    adapter.client.chat_postMessage.assert_awaited_once()
    assert adapter.client.chat_update.await_count >= 1
    # Final update carries the full accumulated text.
    final_call = adapter.client.chat_update.await_args
    assert "Hello world" in final_call.kwargs["text"]
    assert result["id"] == "999.9"


async def test_stream_final_update_sends_full_text(adapter: SlackAdapter) -> None:
    adapter.client.chat_postMessage = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )
    adapter.client.chat_update = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "ts": "999.9", "channel": "C1"}
    )

    async def chunks() -> Any:
        for chunk in ("a", "b", "c"):
            yield chunk

    # Very long interval so only the final update fires.
    await adapter.stream("slack:C1:888.8", chunks(), options={"streamingUpdateIntervalMs": 100_000})
    # Exactly one post + exactly one final update.
    assert adapter.client.chat_postMessage.await_count == 1
    assert adapter.client.chat_update.await_count == 1
    final = adapter.client.chat_update.await_args
    assert "abc" in final.kwargs["text"]


# ---------------------------------------------------------------------------
# Phase 2 — Socket Mode
#
# These tests mirror upstream's ``SocketModeClient`` branch in
# ``adapter-slack/src/index.ts``. The public surface (``handle_webhook``)
# is unchanged — Socket Mode just feeds envelopes to the same
# ``_dispatch_envelope`` helper Phase 1 introduced.
# ---------------------------------------------------------------------------


@pytest.fixture
def socket_adapter(monkeypatch: pytest.MonkeyPatch) -> SlackAdapter:
    """Adapter configured for ``mode="socket"`` (requires app token, not signing)."""

    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    adp = create_slack_adapter({"mode": "socket", "appToken": "xapp-test", "botToken": "xoxb-test"})
    adp._bot_user_id = "U_BOT"
    return adp


# ---------------------------------------------------------------------------
# Cycle 2.1 — ``connect()`` opens a socket when ``mode="socket"``
# ---------------------------------------------------------------------------


async def test_connect_opens_socket_when_mode_is_socket(
    socket_adapter: SlackAdapter,
) -> None:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient

    # Patch the SocketModeClient class so we don't actually open a websocket.
    fake_client = AsyncMock(spec=SocketModeClient)
    fake_client.connect = AsyncMock()
    fake_client.close = AsyncMock()
    # socket_mode_request_listeners is a plain list on the real client.
    fake_client.socket_mode_request_listeners = []

    # Stash the factory so ``connect()`` uses our fake instead of building a real one.
    socket_adapter._socket_client_factory = lambda: fake_client  # type: ignore[attr-defined]

    await socket_adapter.connect()

    fake_client.connect.assert_awaited_once()
    # Listener list must have exactly one entry wired up — our dispatch bridge.
    assert len(fake_client.socket_mode_request_listeners) == 1


async def test_connect_is_noop_in_webhook_mode(adapter: SlackAdapter) -> None:
    # Webhook-mode adapter should ignore ``connect()`` entirely.
    await adapter.connect()
    assert adapter._socket_client is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Cycle 2.2 — ``events_api`` envelope routed to handler
# ---------------------------------------------------------------------------


async def test_events_api_envelope_routes_to_mention_handler(
    socket_adapter: SlackAdapter,
) -> None:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest

    bot = Chat(user_name="bot", adapters={"slack": socket_adapter}, state=create_mock_state())
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def _h(thread: Any, message: Any, context: Any = None) -> None:
        captured["text"] = message.text
        seen.set()

    bot.on_new_mention(_h)

    fake_client = AsyncMock(spec=SocketModeClient)
    fake_client.connect = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.send_socket_mode_response = AsyncMock()
    fake_client.socket_mode_request_listeners = []
    socket_adapter._socket_client_factory = lambda: fake_client  # type: ignore[attr-defined]

    await bot.initialize()

    # The listener should have been registered on connect.
    assert len(fake_client.socket_mode_request_listeners) == 1
    listener = fake_client.socket_mode_request_listeners[0]

    envelope = SocketModeRequest(
        type="events_api",
        envelope_id="env-1",
        payload={
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U2",
                "text": "<@U_BOT> hello",
                "ts": "1234.5",
                "thread_ts": "1234.5",
            },
        },
    )
    await listener(fake_client, envelope)

    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "<@U_BOT> hello"


# ---------------------------------------------------------------------------
# Cycle 2.3 — Every envelope is ack'd (send_socket_mode_response called)
# ---------------------------------------------------------------------------


async def test_every_envelope_is_acked(
    socket_adapter: SlackAdapter,
) -> None:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    bot = Chat(user_name="bot", adapters={"slack": socket_adapter}, state=create_mock_state())

    fake_client = AsyncMock(spec=SocketModeClient)
    fake_client.connect = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.send_socket_mode_response = AsyncMock()
    fake_client.socket_mode_request_listeners = []
    socket_adapter._socket_client_factory = lambda: fake_client  # type: ignore[attr-defined]

    await bot.initialize()
    listener = fake_client.socket_mode_request_listeners[0]

    envelope = SocketModeRequest(
        type="events_api",
        envelope_id="env-ack-1",
        payload={
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U2",
                "text": "<@U_BOT> hi",
                "ts": "1234.5",
                "thread_ts": "1234.5",
            },
        },
    )
    await listener(fake_client, envelope)

    # Ack must have fired, with the envelope ID. Accept either a SocketModeResponse
    # instance or a dict shape — both are valid per the SDK.
    fake_client.send_socket_mode_response.assert_awaited()
    call_arg = fake_client.send_socket_mode_response.await_args.args[0]
    if isinstance(call_arg, SocketModeResponse):
        assert call_arg.envelope_id == "env-ack-1"
    else:
        assert call_arg.get("envelope_id") == "env-ack-1"

    # Let any scheduled dispatch task drain so pytest doesn't warn about
    # pending tasks at test end.
    await asyncio.sleep(0)


async def test_ack_happens_before_dispatch_task_runs(
    socket_adapter: SlackAdapter,
) -> None:
    """Acks must be emitted synchronously (before dispatch) — upstream
    schedules the handler as a task after acking. This prevents Slack
    from timing out and retrying slow handlers."""

    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest

    bot = Chat(user_name="bot", adapters={"slack": socket_adapter}, state=create_mock_state())

    order: list[str] = []

    async def _slow(thread: Any, message: Any, context: Any = None) -> None:
        # Simulate handler work — happens AFTER ack if scheduled as a task.
        await asyncio.sleep(0.05)
        order.append("handler")

    bot.on_new_mention(_slow)

    fake_client = AsyncMock(spec=SocketModeClient)
    fake_client.connect = AsyncMock()
    fake_client.close = AsyncMock()

    async def _record_ack(_resp: Any) -> None:
        order.append("ack")

    fake_client.send_socket_mode_response = AsyncMock(side_effect=_record_ack)
    fake_client.socket_mode_request_listeners = []
    socket_adapter._socket_client_factory = lambda: fake_client  # type: ignore[attr-defined]

    await bot.initialize()
    listener = fake_client.socket_mode_request_listeners[0]

    envelope = SocketModeRequest(
        type="events_api",
        envelope_id="env-order-1",
        payload={
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U2",
                "text": "<@U_BOT> hi",
                "ts": "1234.5",
                "thread_ts": "1234.5",
            },
        },
    )
    await listener(fake_client, envelope)

    # At this point ack must have run; handler must not have finished yet.
    assert order == ["ack"], f"ack must precede handler; got {order}"

    # Wait for the scheduled dispatch task to finish.
    await asyncio.sleep(0.1)
    assert order == ["ack", "handler"]


# ---------------------------------------------------------------------------
# Cycle 2.4 — ``disconnect()`` closes the socket
# ---------------------------------------------------------------------------


async def test_disconnect_closes_socket(socket_adapter: SlackAdapter) -> None:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient

    fake_client = AsyncMock(spec=SocketModeClient)
    fake_client.connect = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.socket_mode_request_listeners = []
    socket_adapter._socket_client_factory = lambda: fake_client  # type: ignore[attr-defined]

    await socket_adapter.connect()
    await socket_adapter.disconnect()

    fake_client.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cycle 2.5 — Chat-level init wires Socket Mode
# ---------------------------------------------------------------------------


async def test_chat_initialize_connects_socket_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    adp = create_slack_adapter({"mode": "socket", "botToken": "xoxb", "appToken": "xapp-test"})
    adp.connect = AsyncMock()  # type: ignore[method-assign]
    bot = Chat(user_name="bot", adapters={"slack": adp}, state=create_mock_state())
    await bot.initialize()
    adp.connect.assert_awaited_once()


async def test_chat_initialize_does_not_connect_in_webhook_mode(
    adapter: SlackAdapter,
) -> None:
    """Webhook-mode adapters must not open a socket on initialize."""
    adapter.connect = AsyncMock()  # type: ignore[method-assign]
    bot = Chat(user_name="bot", adapters={"slack": adapter}, state=create_mock_state())
    await bot.initialize()
    adapter.connect.assert_not_called()
