"""Phase 6 dispatch tests for :class:`WhatsAppAdapter`.

Pins that :meth:`chat.Chat.handle_webhook` round-trips a real WhatsApp Cloud
API ``messages`` payload all the way to a registered
:func:`on_direct_message` handler, proving ``initialize(chat)`` wires
``self._chat`` correctly for the shared dispatch surface.

The HMAC-SHA256 signature over the body is computed here (no extra deps
beyond stdlib ``hmac`` / ``hashlib``) so we exercise the real
``_verify_signature`` path — not a monkeypatch.

WhatsApp is DM-only: every thread is a 1:1 DM between the business phone
number and an end-user ``wa_id``, so mentions do not apply — we assert
against the ``on_direct_message`` handler path.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_whatsapp import create_whatsapp_adapter
from chat_adapter_whatsapp.adapter import WhatsAppAdapter

APP_SECRET = "webhook-test-app-secret-DES-196-phase-6"
ACCESS_TOKEN = "test-access-token"
PHONE_NUMBER_ID = "1234567890"
VERIFY_TOKEN = "test-verify-token"
BOT_USERNAME = "whatsapp-bot"


def _sign(body: bytes) -> str:
    """Compute a WhatsApp ``X-Hub-Signature-256`` value for ``body``."""

    digest = hmac.new(APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> WhatsAppAdapter:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", ACCESS_TOKEN)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", PHONE_NUMBER_ID)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("WHATSAPP_BOT_USERNAME", BOT_USERNAME)
    return create_whatsapp_adapter()


async def test_messages_fires_direct_message_via_chat_handle_webhook(
    adapter: WhatsAppAdapter,
) -> None:
    """Round-trip: Cloud API messages webhook → Chat.handle_webhook → on_direct_message."""

    bot = Chat(
        user_name=BOT_USERNAME,
        adapters={"whatsapp": adapter},
        state=create_mock_state(),
    )
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(
        thread: Any,
        message: Any,
        _channel: Any = None,
        _context: Any = None,
    ) -> None:
        captured["text"] = message.text
        captured["thread_id"] = thread.id if hasattr(thread, "id") else None
        captured["author_user_name"] = message.author.user_name
        seen.set()

    bot.on_direct_message(handler)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100000000000000",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Alice"},
                                    "wa_id": "15557654321",
                                },
                            ],
                            "messages": [
                                {
                                    "id": "wamid.TEST123",
                                    "from": "15557654321",
                                    "timestamp": "1714000000",
                                    "type": "text",
                                    "text": {"body": "please echo this"},
                                },
                            ],
                        },
                    },
                ],
            },
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-hub-signature-256": _sign(body),
        "content-type": "application/json",
    }

    status, _resp_headers, resp_body = await bot.handle_webhook("whatsapp", body, headers)
    assert status == 200
    assert resp_body == "ok"

    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "please echo this"
    assert captured["author_user_name"] == "Alice"
    assert captured["thread_id"] is not None


async def test_invalid_signature_returns_401(adapter: WhatsAppAdapter) -> None:
    """Tampered body → ``_verify_signature`` rejects → 401."""

    payload = {"object": "whatsapp_business_account", "entry": []}
    body = json.dumps(payload).encode("utf-8")
    # Sign a *different* body so the header is valid-shaped but wrong.
    headers = {
        "x-hub-signature-256": _sign(body + b"-tampered"),
        "content-type": "application/json",
    }
    status, _headers, _body = await adapter.handle_webhook(body, headers)
    assert status == 401


async def test_initialize_stores_chat_reference(adapter: WhatsAppAdapter) -> None:
    """Confirm ``initialize(chat)`` wires ``self._chat`` correctly.

    Mirrors the Discord / GitHub phase guards that caught wiring bugs
    elsewhere.
    """

    sentinel = object()
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel
    # ``initialize`` also seeds the bot user id from the phone number id.
    assert adapter._bot_user_id == PHONE_NUMBER_ID
