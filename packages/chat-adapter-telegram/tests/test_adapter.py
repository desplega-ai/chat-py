"""Tests for the Telegram adapter facade — webhook verification, REST ops,
update dispatch, and lifecycle.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    ValidationError,
)
from chat_adapter_telegram import (
    TELEGRAM_API_BASE,
    TELEGRAM_SECRET_TOKEN_HEADER,
    TelegramAdapter,
    apply_telegram_entities,
    create_telegram_adapter,
)

BOT_TOKEN = "test:token"
API_BASE_URL = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}"


def _make_adapter(**overrides: Any) -> TelegramAdapter:
    config = {"botToken": BOT_TOKEN, "secretToken": "shh", **overrides}
    return TelegramAdapter(config)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construct_requires_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        TelegramAdapter()
    assert "botToken" in str(exc_info.value)


def test_construct_reads_env_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env:token")
    adapter = TelegramAdapter()
    assert adapter.name == "telegram"


def test_construct_rejects_invalid_mode() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TelegramAdapter({"botToken": BOT_TOKEN, "mode": "bogus"})  # type: ignore[typeddict-item]
    assert "Invalid mode" in str(exc_info.value)


def test_user_name_strips_leading_at() -> None:
    adapter = TelegramAdapter({"botToken": BOT_TOKEN, "userName": "@mybot"})
    assert adapter.user_name == "mybot"


def test_api_base_url_trims_trailing_slash() -> None:
    adapter = TelegramAdapter(
        {"botToken": BOT_TOKEN, "apiUrl": "https://example.com///"},
    )
    # _api_base_url is private but we can assert via fetch URL on an outbound call.
    assert adapter._api_base_url == "https://example.com"


def test_create_telegram_adapter_factory() -> None:
    adapter = create_telegram_adapter({"botToken": BOT_TOKEN})
    assert isinstance(adapter, TelegramAdapter)


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------


async def test_webhook_rejects_missing_secret_token() -> None:
    adapter = _make_adapter()
    status, _h, body = await adapter.handle_webhook(b"{}", {})
    assert status == 401
    assert "Invalid secret token" in body
    await adapter.close()


async def test_webhook_rejects_wrong_secret_token() -> None:
    adapter = _make_adapter()
    status, _h, _b = await adapter.handle_webhook(
        b"{}",
        {TELEGRAM_SECRET_TOKEN_HEADER: "bad"},
    )
    assert status == 401
    await adapter.close()


async def test_webhook_accepts_valid_secret_token_without_chat() -> None:
    adapter = _make_adapter()
    status, _h, body = await adapter.handle_webhook(
        b"{}",
        {TELEGRAM_SECRET_TOKEN_HEADER: "shh"},
    )
    assert status == 200
    assert body == "OK"
    await adapter.close()


async def test_webhook_rejects_invalid_json() -> None:
    adapter = _make_adapter()
    status, _h, body = await adapter.handle_webhook(
        b"not json{",
        {TELEGRAM_SECRET_TOKEN_HEADER: "shh"},
    )
    assert status == 400
    assert "Invalid JSON" in body
    await adapter.close()


async def test_webhook_without_secret_token_warns_once() -> None:
    adapter = TelegramAdapter({"botToken": BOT_TOKEN})
    # Attach a spy logger so we can count warn calls.
    adapter._logger = MagicMock()
    status, _h, _b = await adapter.handle_webhook(b"{}", {})
    assert status == 200
    await adapter.handle_webhook(b"{}", {})
    # warn called exactly once for the "no verification" message.
    warn_messages = [c.args[0] for c in adapter._logger.warn.call_args_list]
    verification_warnings = [
        m for m in warn_messages if "Telegram webhook verification is disabled" in m
    ]
    assert len(verification_warnings) == 1
    await adapter.close()


async def test_webhook_headers_case_insensitive() -> None:
    adapter = _make_adapter()
    status, _h, _b = await adapter.handle_webhook(
        b"{}",
        {"X-Telegram-Bot-Api-Secret-Token": "shh"},
    )
    assert status == 200
    await adapter.close()


# ---------------------------------------------------------------------------
# Update dispatch
# ---------------------------------------------------------------------------


async def test_webhook_dispatches_message_update() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_message = MagicMock()
    adapter._chat = chat_mock

    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "date": 1700000000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 999, "first_name": "User", "is_bot": False},
            "text": "hi",
        },
    }
    status, _h, _b = await adapter.handle_webhook(
        json.dumps(update).encode(),
        {TELEGRAM_SECRET_TOKEN_HEADER: "shh"},
    )
    assert status == 200
    chat_mock.process_message.assert_called_once()
    args = chat_mock.process_message.call_args
    assert args.args[1] == "telegram:42"
    await adapter.close()


async def test_webhook_dispatches_callback_query() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_action = MagicMock()
    adapter._chat = chat_mock

    # Patch fetch to avoid a real network call in the ack task.
    adapter._telegram_fetch = AsyncMock(return_value=True)

    update = {
        "update_id": 2,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 7, "first_name": "Clicker", "is_bot": False},
            "data": 'chat:{"a":"vote","v":"yes"}',
            "message": {
                "message_id": 50,
                "date": 1700000000,
                "chat": {"id": 42, "type": "private"},
            },
        },
    }
    status, _h, _b = await adapter.handle_webhook(
        json.dumps(update).encode(),
        {TELEGRAM_SECRET_TOKEN_HEADER: "shh"},
    )
    assert status == 200
    chat_mock.process_action.assert_called_once()
    event = chat_mock.process_action.call_args.args[0]
    assert event["actionId"] == "vote"
    assert event["value"] == "yes"
    assert event["threadId"] == "telegram:42"
    assert event["messageId"] == "42:50"
    await adapter.close()


async def test_webhook_dispatches_reaction_update() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_reaction = MagicMock()
    adapter._chat = chat_mock

    update = {
        "update_id": 3,
        "message_reaction": {
            "chat": {"id": -100, "type": "supergroup"},
            "message_id": 77,
            "user": {"id": 8, "first_name": "Reactor", "is_bot": False},
            "old_reaction": [],
            "new_reaction": [{"type": "emoji", "emoji": "👍"}],
        },
    }
    status, _h, _b = await adapter.handle_webhook(
        json.dumps(update).encode(),
        {TELEGRAM_SECRET_TOKEN_HEADER: "shh"},
    )
    assert status == 200
    chat_mock.process_reaction.assert_called_once()
    event = chat_mock.process_reaction.call_args.args[0]
    assert event["added"] is True
    assert event["rawEmoji"] == "👍"
    await adapter.close()


# ---------------------------------------------------------------------------
# REST: sendMessage / editMessageText / deleteMessage
# ---------------------------------------------------------------------------


@respx.mock
async def test_post_message_sends_text() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 1,
                    "date": 1700000000,
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 1, "first_name": "Bot", "is_bot": True},
                    "text": "Hello",
                },
            },
        ),
    )
    result = await adapter.post_message("telegram:42", "Hello")
    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["chat_id"] == "42"
    assert body["text"] == "Hello"
    # Plain string ⇒ parse_mode omitted.
    assert "parse_mode" not in body
    assert result["id"] == "42:1"
    assert result["threadId"] == "telegram:42"
    await adapter.close()


@respx.mock
async def test_post_message_markdown_sets_parse_mode() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 2,
                    "date": 1700000000,
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 1, "first_name": "Bot", "is_bot": True},
                    "text": "*hi*",
                },
            },
        ),
    )
    await adapter.post_message("telegram:42", {"markdown": "**hi**"})
    body = json.loads(route.calls.last.request.content.decode())
    assert body["parse_mode"] == "MarkdownV2"
    assert "*hi*" in body["text"]
    await adapter.close()


@respx.mock
async def test_post_message_empty_text_raises() -> None:
    adapter = _make_adapter()
    respx.post(f"{API_BASE_URL}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}}),
    )
    with pytest.raises(ValidationError):
        await adapter.post_message("telegram:42", "   ")
    await adapter.close()


@respx.mock
async def test_post_message_with_topic_forwards_thread_id() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 1,
                    "date": 1700000000,
                    "chat": {"id": -100, "type": "supergroup"},
                    "message_thread_id": 42,
                    "from": {"id": 1, "first_name": "Bot", "is_bot": True},
                    "text": "hi",
                },
            },
        ),
    )
    await adapter.post_message("telegram:-100:42", "hi")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["chat_id"] == "-100"
    assert body["message_thread_id"] == 42
    await adapter.close()


@respx.mock
async def test_edit_message_sends_and_returns_message() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/editMessageText").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 10,
                    "date": 1700000000,
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 1, "first_name": "Bot", "is_bot": True},
                    "text": "edited",
                },
            },
        ),
    )
    result = await adapter.edit_message("telegram:42", "42:10", "edited")
    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["chat_id"] == "42"
    assert body["message_id"] == 10
    assert result["id"] == "42:10"
    await adapter.close()


@respx.mock
async def test_delete_message_hits_endpoint() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/deleteMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True}),
    )
    await adapter.delete_message("telegram:42", "42:5")
    body = json.loads(route.calls.last.request.content.decode())
    assert body == {"chat_id": "42", "message_id": 5}
    await adapter.close()


@respx.mock
async def test_start_typing_sends_chat_action() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/sendChatAction").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True}),
    )
    await adapter.start_typing("telegram:42")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["action"] == "typing"
    assert body["chat_id"] == "42"
    await adapter.close()


@respx.mock
async def test_add_reaction_calls_set_message_reaction() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/setMessageReaction").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True}),
    )
    await adapter.add_reaction("telegram:42", "42:1", "👍")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["reaction"] == [{"type": "emoji", "emoji": "👍"}]
    await adapter.close()


@respx.mock
async def test_remove_reaction_sends_empty_list() -> None:
    adapter = _make_adapter()
    route = respx.post(f"{API_BASE_URL}/setMessageReaction").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True}),
    )
    await adapter.remove_reaction("telegram:42", "42:1", "👍")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["reaction"] == []
    await adapter.close()


@respx.mock
async def test_bot_api_429_raises_rate_limit() -> None:
    adapter = _make_adapter()
    respx.post(f"{API_BASE_URL}/sendMessage").mock(
        return_value=httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 5},
            },
        ),
    )
    with pytest.raises(AdapterRateLimitError) as exc_info:
        await adapter.post_message("telegram:42", "hi")
    assert getattr(exc_info.value, "retry_after", None) == 5
    await adapter.close()


@respx.mock
async def test_bot_api_401_raises_auth_error() -> None:
    adapter = _make_adapter()
    respx.post(f"{API_BASE_URL}/sendMessage").mock(
        return_value=httpx.Response(
            401,
            json={"ok": False, "error_code": 401, "description": "Unauthorized"},
        ),
    )
    with pytest.raises(AuthenticationError):
        await adapter.post_message("telegram:42", "hi")
    await adapter.close()


# ---------------------------------------------------------------------------
# Fetch / channel-info / DM
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_thread_reads_get_chat() -> None:
    adapter = _make_adapter()
    respx.post(f"{API_BASE_URL}/getChat").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"id": 42, "type": "private", "first_name": "Alice"},
            },
        ),
    )
    result = await adapter.fetch_thread("telegram:42")
    assert result["id"] == "telegram:42"
    assert result["channelId"] == "42"
    assert result["isDM"] is True
    assert result["channelName"] == "Alice"
    await adapter.close()


async def test_channel_id_from_thread_id_chat_only() -> None:
    adapter = _make_adapter()
    assert adapter.channel_id_from_thread_id("telegram:42") == "telegram:42"
    await adapter.close()


async def test_channel_id_from_thread_id_topic() -> None:
    adapter = _make_adapter()
    assert adapter.channel_id_from_thread_id("telegram:42:5") == "telegram:42"
    await adapter.close()


async def test_open_dm_returns_thread_id() -> None:
    adapter = _make_adapter()
    assert await adapter.open_dm("123") == "telegram:123"
    await adapter.close()


async def test_is_dm_based_on_chat_id_sign() -> None:
    adapter = _make_adapter()
    assert adapter.is_dm("telegram:123") is True
    assert adapter.is_dm("telegram:-100") is False
    await adapter.close()


async def test_decode_composite_message_id_roundtrip() -> None:
    adapter = _make_adapter()
    chat_id, message_id, composite = adapter._decode_composite_message_id(
        "42:123",
        "42",
    )
    assert (chat_id, message_id, composite) == ("42", 123, "42:123")
    await adapter.close()


async def test_decode_composite_message_id_bare_int_with_expected_chat() -> None:
    adapter = _make_adapter()
    chat_id, message_id, composite = adapter._decode_composite_message_id(
        "7",
        "42",
    )
    assert (chat_id, message_id, composite) == ("42", 7, "42:7")
    await adapter.close()


async def test_decode_composite_message_id_mismatch_raises() -> None:
    adapter = _make_adapter()
    with pytest.raises(ValidationError):
        adapter._decode_composite_message_id("5:1", "42")
    await adapter.close()


async def test_decode_composite_message_id_bare_invalid_raises() -> None:
    adapter = _make_adapter()
    with pytest.raises(ValidationError):
        adapter._decode_composite_message_id("abc")
    await adapter.close()


# ---------------------------------------------------------------------------
# apply_telegram_entities
# ---------------------------------------------------------------------------


def test_apply_entities_empty_list_returns_text() -> None:
    assert apply_telegram_entities("hi", []) == "hi"


def test_apply_entities_bold() -> None:
    result = apply_telegram_entities(
        "Hello world",
        [{"offset": 6, "length": 5, "type": "bold"}],
    )
    assert result == "Hello **world**"


def test_apply_entities_italic() -> None:
    result = apply_telegram_entities(
        "ab c",
        [{"offset": 3, "length": 1, "type": "italic"}],
    )
    assert result == "ab *c*"


def test_apply_entities_code() -> None:
    result = apply_telegram_entities(
        "run x now",
        [{"offset": 4, "length": 1, "type": "code"}],
    )
    assert result == "run `x` now"


def test_apply_entities_pre_with_language() -> None:
    result = apply_telegram_entities(
        "abc",
        [{"offset": 0, "length": 3, "type": "pre", "language": "py"}],
    )
    assert result == "```py\nabc\n```"


def test_apply_entities_strikethrough() -> None:
    result = apply_telegram_entities(
        "ab cd",
        [{"offset": 3, "length": 2, "type": "strikethrough"}],
    )
    assert result == "ab ~~cd~~"


def test_apply_entities_text_link_escapes_brackets() -> None:
    result = apply_telegram_entities(
        "hi [ok]",
        [{"offset": 3, "length": 4, "type": "text_link", "url": "https://x.com"}],
    )
    assert result == r"hi [\[ok\]](https://x.com)"


def test_apply_entities_unknown_type_ignored() -> None:
    # URL entity is usually already visible in text; we should leave it alone.
    result = apply_telegram_entities(
        "see https://x.com",
        [{"offset": 4, "length": 13, "type": "url"}],
    )
    assert result == "see https://x.com"


def test_apply_entities_order_non_overlapping() -> None:
    # Two non-overlapping entities are applied in descending offset order.
    result = apply_telegram_entities(
        "hello world!",
        [
            {"offset": 0, "length": 5, "type": "bold"},
            {"offset": 6, "length": 5, "type": "italic"},
        ],
    )
    assert "**hello**" in result
    assert "*world*" in result
