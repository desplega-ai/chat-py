"""Tests for the WhatsApp adapter facade.

Covers construction, webhook verification (GET challenge + POST HMAC),
inbound dispatch (message / reaction / interactive reply / button), REST
operations, and error mapping.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    ValidationError,
)
from chat_adapter_whatsapp import (
    DEFAULT_API_BASE_URL,
    DEFAULT_API_VERSION,
    WHATSAPP_MESSAGE_LIMIT,
    WhatsAppAdapter,
    create_whatsapp_adapter,
    encode_whatsapp_callback_data,
    split_message,
)

ACCESS_TOKEN = "EAA-test-access-token"
APP_SECRET = "shh-app-secret"
PHONE_NUMBER_ID = "111222333"
VERIFY_TOKEN = "verify-token-x"
USER_WA_ID = "15555550100"
THREAD_ID = f"whatsapp:{PHONE_NUMBER_ID}:{USER_WA_ID}"
GRAPH_BASE = f"{DEFAULT_API_BASE_URL}/{DEFAULT_API_VERSION}"
MESSAGES_URL = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"


def _make_adapter(**overrides: Any) -> WhatsAppAdapter:
    config: dict[str, Any] = {
        "accessToken": ACCESS_TOKEN,
        "appSecret": APP_SECRET,
        "phoneNumberId": PHONE_NUMBER_ID,
        "verifyToken": VERIFY_TOKEN,
        **overrides,
    }
    return WhatsAppAdapter(config)


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construct_requires_access_token() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WhatsAppAdapter(
            {  # type: ignore[arg-type]
                "appSecret": APP_SECRET,
                "phoneNumberId": PHONE_NUMBER_ID,
                "verifyToken": VERIFY_TOKEN,
            },
        )
    assert "accessToken" in str(exc_info.value)


def test_construct_requires_app_secret() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WhatsAppAdapter(
            {  # type: ignore[arg-type]
                "accessToken": ACCESS_TOKEN,
                "phoneNumberId": PHONE_NUMBER_ID,
                "verifyToken": VERIFY_TOKEN,
            },
        )
    assert "appSecret" in str(exc_info.value)


def test_construct_requires_phone_number_id() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WhatsAppAdapter(
            {  # type: ignore[arg-type]
                "accessToken": ACCESS_TOKEN,
                "appSecret": APP_SECRET,
                "verifyToken": VERIFY_TOKEN,
            },
        )
    assert "phoneNumberId" in str(exc_info.value)


def test_construct_requires_verify_token() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WhatsAppAdapter(
            {  # type: ignore[arg-type]
                "accessToken": ACCESS_TOKEN,
                "appSecret": APP_SECRET,
                "phoneNumberId": PHONE_NUMBER_ID,
            },
        )
    assert "verifyToken" in str(exc_info.value)


def test_construct_defaults_user_name() -> None:
    adapter = _make_adapter()
    assert adapter.user_name == "whatsapp-bot"


def test_construct_uses_custom_user_name() -> None:
    adapter = _make_adapter(userName="MyBot")
    assert adapter.user_name == "MyBot"


def test_construct_trims_trailing_slash_in_api_url() -> None:
    adapter = _make_adapter(apiUrl="https://example.com///")
    assert adapter._graph_api_url == f"https://example.com/{DEFAULT_API_VERSION}"


def test_factory_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "env-tok")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "env-sec")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "env-phone")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "env-verify")
    adapter = create_whatsapp_adapter()
    assert adapter.name == "whatsapp"


def test_factory_requires_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        create_whatsapp_adapter(
            {  # type: ignore[arg-type]
                "appSecret": APP_SECRET,
                "phoneNumberId": PHONE_NUMBER_ID,
                "verifyToken": VERIFY_TOKEN,
            },
        )
    assert "accessToken" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Webhook GET — verification challenge
# ---------------------------------------------------------------------------


async def test_webhook_get_challenge_succeeds() -> None:
    adapter = _make_adapter()
    status, _h, body = await adapter.handle_webhook(
        b"",
        {},
        method="GET",
        url=(
            "https://example.com/webhook?hub.mode=subscribe"
            f"&hub.verify_token={VERIFY_TOKEN}&hub.challenge=abc123"
        ),
    )
    assert status == 200
    assert body == "abc123"
    await adapter.close()


async def test_webhook_get_challenge_wrong_token() -> None:
    adapter = _make_adapter()
    status, _h, body = await adapter.handle_webhook(
        b"",
        {},
        method="GET",
        url=(
            "https://example.com/webhook?hub.mode=subscribe"
            "&hub.verify_token=wrong&hub.challenge=abc123"
        ),
    )
    assert status == 403
    assert "Forbidden" in body
    await adapter.close()


async def test_webhook_get_challenge_wrong_mode() -> None:
    adapter = _make_adapter()
    status, _h, _body = await adapter.handle_webhook(
        b"",
        {},
        method="GET",
        url=(
            "https://example.com/webhook?hub.mode=other"
            f"&hub.verify_token={VERIFY_TOKEN}&hub.challenge=abc123"
        ),
    )
    assert status == 403
    await adapter.close()


# ---------------------------------------------------------------------------
# Webhook POST — signature verification
# ---------------------------------------------------------------------------


async def test_webhook_rejects_missing_signature() -> None:
    adapter = _make_adapter()
    status, _h, body = await adapter.handle_webhook(b"{}", {})
    assert status == 401
    assert "Invalid signature" in body
    await adapter.close()


async def test_webhook_rejects_wrong_signature() -> None:
    adapter = _make_adapter()
    status, _h, _body = await adapter.handle_webhook(
        b"{}",
        {"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert status == 401
    await adapter.close()


async def test_webhook_accepts_valid_signature() -> None:
    adapter = _make_adapter()
    body = b"{}"
    status, _h, response_body = await adapter.handle_webhook(
        body,
        {"X-Hub-Signature-256": _sign(body)},
    )
    assert status == 200
    assert response_body == "ok"
    await adapter.close()


async def test_webhook_headers_case_insensitive() -> None:
    adapter = _make_adapter()
    body = b"{}"
    status, _h, _body = await adapter.handle_webhook(
        body,
        {"x-hub-signature-256": _sign(body)},
    )
    assert status == 200
    await adapter.close()


async def test_webhook_rejects_invalid_json() -> None:
    adapter = _make_adapter()
    body = b"not-json{"
    status, _h, response_body = await adapter.handle_webhook(
        body,
        {"X-Hub-Signature-256": _sign(body)},
    )
    assert status == 400
    assert "Invalid JSON" in response_body
    await adapter.close()


# ---------------------------------------------------------------------------
# Inbound dispatch — text message
# ---------------------------------------------------------------------------


def _wrap_change(
    inbound: dict[str, Any],
    contacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "wba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+1555000",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": contacts or [],
                            "messages": [inbound],
                        },
                    },
                ],
            },
        ],
    }


async def test_webhook_dispatches_text_message() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_message = MagicMock()
    adapter._chat = chat_mock

    payload = _wrap_change(
        {
            "id": "wamid.HBgL",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "text",
            "text": {"body": "hi"},
        },
        contacts=[
            {
                "wa_id": USER_WA_ID,
                "profile": {"name": "Alice"},
            },
        ],
    )
    body = json.dumps(payload).encode("utf-8")
    status, _h, _b = await adapter.handle_webhook(
        body,
        {"X-Hub-Signature-256": _sign(body)},
    )
    assert status == 200
    chat_mock.process_message.assert_called_once()
    args = chat_mock.process_message.call_args
    assert args.args[1] == THREAD_ID
    message = args.args[2]
    assert message.text == "hi"
    assert message.thread_id == THREAD_ID
    assert message.author.user_name == "Alice"
    await adapter.close()


async def test_webhook_dispatches_reaction() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_reaction = MagicMock()
    adapter._chat = chat_mock

    payload = _wrap_change(
        {
            "id": "wamid.R",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "reaction",
            "reaction": {"message_id": "wamid.X", "emoji": "\U0001f44d"},
        },
    )
    body = json.dumps(payload).encode("utf-8")
    status, _h, _b = await adapter.handle_webhook(
        body,
        {"X-Hub-Signature-256": _sign(body)},
    )
    assert status == 200
    chat_mock.process_reaction.assert_called_once()
    event = chat_mock.process_reaction.call_args.args[0]
    assert event["added"] is True
    assert event["rawEmoji"] == "\U0001f44d"
    assert event["threadId"] == THREAD_ID
    assert event["messageId"] == "wamid.X"
    await adapter.close()


async def test_webhook_dispatches_reaction_removal() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_reaction = MagicMock()
    adapter._chat = chat_mock

    payload = _wrap_change(
        {
            "id": "wamid.R2",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "reaction",
            "reaction": {"message_id": "wamid.X", "emoji": ""},
        },
    )
    body = json.dumps(payload).encode("utf-8")
    await adapter.handle_webhook(body, {"X-Hub-Signature-256": _sign(body)})
    event = chat_mock.process_reaction.call_args.args[0]
    assert event["added"] is False
    await adapter.close()


async def test_webhook_dispatches_interactive_button_reply() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_action = MagicMock()
    adapter._chat = chat_mock

    callback = encode_whatsapp_callback_data("vote", "yes")
    payload = _wrap_change(
        {
            "id": "wamid.I",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": callback, "title": "Yes"},
            },
        },
    )
    body = json.dumps(payload).encode("utf-8")
    await adapter.handle_webhook(body, {"X-Hub-Signature-256": _sign(body)})
    event = chat_mock.process_action.call_args.args[0]
    assert event["actionId"] == "vote"
    assert event["value"] == "yes"
    assert event["threadId"] == THREAD_ID
    await adapter.close()


async def test_webhook_dispatches_interactive_list_reply() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_action = MagicMock()
    adapter._chat = chat_mock

    payload = _wrap_change(
        {
            "id": "wamid.L",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "interactive",
            "interactive": {
                "type": "list_reply",
                "list_reply": {"id": "row-1", "title": "Row One"},
            },
        },
    )
    body = json.dumps(payload).encode("utf-8")
    await adapter.handle_webhook(body, {"X-Hub-Signature-256": _sign(body)})
    event = chat_mock.process_action.call_args.args[0]
    # Non-prefixed ID falls through as both actionId and value.
    assert event["actionId"] == "row-1"
    await adapter.close()


async def test_webhook_dispatches_button_response() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_action = MagicMock()
    adapter._chat = chat_mock

    payload = _wrap_change(
        {
            "id": "wamid.B",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "button",
            "button": {"payload": "approve", "text": "Approve"},
        },
    )
    body = json.dumps(payload).encode("utf-8")
    await adapter.handle_webhook(body, {"X-Hub-Signature-256": _sign(body)})
    event = chat_mock.process_action.call_args.args[0]
    assert event["actionId"] == "approve"
    assert event["value"] == "Approve"
    await adapter.close()


async def test_webhook_ignores_unsupported_message_type() -> None:
    adapter = _make_adapter()
    chat_mock = MagicMock()
    chat_mock.process_message = MagicMock()
    adapter._chat = chat_mock

    payload = _wrap_change(
        {
            "id": "wamid.U",
            "from": USER_WA_ID,
            "timestamp": "1700000000",
            "type": "unknown",
        },
    )
    body = json.dumps(payload).encode("utf-8")
    status, _h, _b = await adapter.handle_webhook(
        body,
        {"X-Hub-Signature-256": _sign(body)},
    )
    assert status == 200
    chat_mock.process_message.assert_not_called()
    await adapter.close()


# ---------------------------------------------------------------------------
# REST: post_message
# ---------------------------------------------------------------------------


def _send_response(message_id: str = "wamid.OUT") -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "contacts": [{"input": USER_WA_ID, "wa_id": USER_WA_ID}],
        "messages": [{"id": message_id}],
    }


@respx.mock
async def test_post_message_sends_text() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=_send_response("wamid.A")),
    )
    result = await adapter.post_message(THREAD_ID, "hello world")
    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == USER_WA_ID
    assert body["type"] == "text"
    assert body["text"]["body"] == "hello world"
    assert result["id"] == "wamid.A"
    assert result["threadId"] == THREAD_ID
    await adapter.close()


@respx.mock
async def test_post_message_renders_markdown_to_whatsapp_format() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=_send_response()),
    )
    await adapter.post_message(THREAD_ID, {"markdown": "**hi**"})
    body = json.loads(route.calls.last.request.content.decode())
    assert "*hi*" in body["text"]["body"]
    assert "**hi**" not in body["text"]["body"]
    await adapter.close()


@respx.mock
async def test_post_message_card_with_buttons_sends_interactive() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=_send_response("wamid.I")),
    )
    card = {
        "type": "card",
        "title": "Pick one",
        "children": [
            {"type": "text", "content": "Body text"},
            {
                "type": "actions",
                "children": [
                    {"type": "button", "id": "yes", "label": "Yes"},
                    {"type": "button", "id": "no", "label": "No"},
                ],
            },
        ],
    }
    await adapter.post_message(THREAD_ID, card)
    body = json.loads(route.calls.last.request.content.decode())
    assert body["type"] == "interactive"
    assert body["interactive"]["type"] == "button"
    assert len(body["interactive"]["action"]["buttons"]) == 2
    await adapter.close()


@respx.mock
async def test_post_message_long_text_splits_into_chunks() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=_send_response()),
    )
    text = "x" * (WHATSAPP_MESSAGE_LIMIT * 2 + 50)
    await adapter.post_message(THREAD_ID, text)
    # Long messages cause more than one POST request.
    assert route.call_count >= 2
    await adapter.close()


# ---------------------------------------------------------------------------
# REST: edit / delete
# ---------------------------------------------------------------------------


async def test_edit_message_raises_not_implemented() -> None:
    adapter = _make_adapter()
    with pytest.raises(ChatNotImplementedError):
        await adapter.edit_message(THREAD_ID, "wamid.X", "hi")
    await adapter.close()


async def test_delete_message_raises_not_implemented() -> None:
    adapter = _make_adapter()
    with pytest.raises(ChatNotImplementedError):
        await adapter.delete_message(THREAD_ID, "wamid.X")
    await adapter.close()


# ---------------------------------------------------------------------------
# REST: reactions / mark_as_read / typing
# ---------------------------------------------------------------------------


@respx.mock
async def test_add_reaction_sends_emoji() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json={"messaging_product": "whatsapp"}),
    )
    await adapter.add_reaction(THREAD_ID, "wamid.X", "\U0001f44d")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["type"] == "reaction"
    assert body["reaction"]["message_id"] == "wamid.X"
    assert body["reaction"]["emoji"] == "\U0001f44d"
    await adapter.close()


@respx.mock
async def test_remove_reaction_sends_empty_emoji() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json={"messaging_product": "whatsapp"}),
    )
    await adapter.remove_reaction(THREAD_ID, "wamid.X", "\U0001f44d")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["reaction"]["emoji"] == ""
    await adapter.close()


@respx.mock
async def test_mark_as_read_calls_messages_endpoint() -> None:
    adapter = _make_adapter()
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json={"messaging_product": "whatsapp"}),
    )
    await adapter.mark_as_read("wamid.X")
    body = json.loads(route.calls.last.request.content.decode())
    assert body == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.X",
    }
    await adapter.close()


async def test_start_typing_is_no_op() -> None:
    adapter = _make_adapter()
    # Should not raise and should not perform a network call.
    await adapter.start_typing(THREAD_ID)
    await adapter.close()


# ---------------------------------------------------------------------------
# fetch_messages / fetch_thread / dm helpers
# ---------------------------------------------------------------------------


async def test_fetch_messages_returns_empty() -> None:
    adapter = _make_adapter()
    result = await adapter.fetch_messages(THREAD_ID)
    assert result == {"messages": []}
    await adapter.close()


async def test_fetch_thread_returns_metadata() -> None:
    adapter = _make_adapter()
    result = await adapter.fetch_thread(THREAD_ID)
    assert result["id"] == THREAD_ID
    assert result["channelId"] == f"whatsapp:{PHONE_NUMBER_ID}"
    assert result["isDM"] is True
    assert result["metadata"]["userWaId"] == USER_WA_ID
    await adapter.close()


async def test_is_dm_always_true() -> None:
    adapter = _make_adapter()
    assert adapter.is_dm(THREAD_ID) is True
    await adapter.close()


async def test_open_dm_returns_thread_id() -> None:
    adapter = _make_adapter()
    result = await adapter.open_dm(USER_WA_ID)
    assert result == THREAD_ID
    await adapter.close()


async def test_channel_id_from_thread_id_is_thread_id() -> None:
    adapter = _make_adapter()
    assert adapter.channel_id_from_thread_id(THREAD_ID) == THREAD_ID
    await adapter.close()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@respx.mock
async def test_429_raises_rate_limit_error() -> None:
    adapter = _make_adapter()
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "Rate limit", "code": 80007}},
        ),
    )
    with pytest.raises(AdapterRateLimitError):
        await adapter.post_message(THREAD_ID, "hi")
    await adapter.close()


@respx.mock
async def test_401_raises_authentication_error() -> None:
    adapter = _make_adapter()
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            401,
            json={"error": {"message": "Invalid token", "code": 190}},
        ),
    )
    with pytest.raises(AuthenticationError):
        await adapter.post_message(THREAD_ID, "hi")
    await adapter.close()


@respx.mock
async def test_network_failure_raises_network_error() -> None:
    adapter = _make_adapter()
    respx.post(MESSAGES_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(NetworkError):
        await adapter.post_message(THREAD_ID, "hi")
    await adapter.close()


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------


def test_split_message_short_returns_single_chunk() -> None:
    assert split_message("hello") == ["hello"]


def test_split_message_long_splits_on_paragraph_boundary() -> None:
    para = "a" * (WHATSAPP_MESSAGE_LIMIT - 100)
    text = para + "\n\n" + "b" * 200
    chunks = split_message(text)
    assert len(chunks) == 2
    assert all(len(c) <= WHATSAPP_MESSAGE_LIMIT for c in chunks)


def test_split_message_falls_back_to_hard_break() -> None:
    text = "x" * (WHATSAPP_MESSAGE_LIMIT * 2 + 50)
    chunks = split_message(text)
    assert all(len(c) <= WHATSAPP_MESSAGE_LIMIT for c in chunks)
    assert "".join(chunks) == text
