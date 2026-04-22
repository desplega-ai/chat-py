"""Phase 9 dispatch tests for :class:`TelegramAdapter`.

Pins that :meth:`chat.Chat.handle_webhook` round-trips a real Telegram
``message`` update all the way to a registered handler — proving
``initialize(chat)`` wires ``self._chat`` correctly for the shared
dispatch surface.

Also audits:
- The ``x-telegram-bot-api-secret-token`` header is honoured — tampered
  tokens return ``401`` without invoking any handler.
- The ``message.from`` payload is serialised into an :class:`Author`
  dataclass (not a dict) — guarding against the "author dict" regression
  observed in other adapters where ``message.author`` was left as a dict
  and downstream ``message.author.user_name`` access crashed.
- Direct-message routing (positive Telegram chat id) fires
  ``on_direct_message`` rather than ``on_new_mention``.
- Group-chat mentions (negative Telegram chat id + ``@bot`` mention
  entity) fire ``on_new_mention``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_telegram import (
    TELEGRAM_SECRET_TOKEN_HEADER,
    create_telegram_adapter,
)
from chat_adapter_telegram.adapter import TelegramAdapter

BOT_TOKEN = "bot-test-token"
WEBHOOK_SECRET_TOKEN = "webhook-secret-DES-196-phase-9"
BOT_USERNAME = "chat_py_bot"


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> TelegramAdapter:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", WEBHOOK_SECRET_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", BOT_USERNAME)
    a = create_telegram_adapter()
    # Pre-set bot id so ``is_mention`` detection via ``text_mention`` works
    # without an outbound ``getMe`` call.
    a._bot_user_id = "9999"
    return a


def _direct_message_body() -> bytes:
    payload = {
        "update_id": 10_000_001,
        "message": {
            "message_id": 42,
            "date": 1_714_000_000,
            "chat": {
                "id": 12345,  # positive => DM per TelegramAdapter.is_dm
                "type": "private",
                "username": "alice_tg",
                "first_name": "Alice",
            },
            "from": {
                "id": 12345,
                "is_bot": False,
                "first_name": "Alice",
                "last_name": "Example",
                "username": "alice_tg",
            },
            "text": "hello from the dm",
        },
    }
    return json.dumps(payload).encode("utf-8")


def _group_mention_body() -> bytes:
    mention = f"@{BOT_USERNAME}"
    text = f"{mention} please echo this"
    return json.dumps(
        {
            "update_id": 10_000_002,
            "message": {
                "message_id": 43,
                "date": 1_714_000_100,
                "chat": {
                    "id": -100987654321,  # negative => group
                    "type": "supergroup",
                    "title": "Test Group",
                },
                "from": {
                    "id": 12345,
                    "is_bot": False,
                    "first_name": "Alice",
                    "username": "alice_tg",
                },
                "text": text,
                "entities": [
                    {"type": "mention", "offset": 0, "length": len(mention)},
                ],
            },
        },
    ).encode("utf-8")


async def _build_bot(adapter: TelegramAdapter) -> Chat:
    bot = Chat(
        user_name=BOT_USERNAME,
        adapters={"telegram": adapter},
        state=create_mock_state(),
    )
    # Bind chat to adapter directly — skips the outbound ``getMe`` probe in
    # ``initialize`` so tests stay hermetic.
    adapter._chat = bot
    return bot


async def test_direct_message_fires_on_direct_message_via_chat_handle_webhook(
    adapter: TelegramAdapter,
) -> None:
    bot = await _build_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(
        thread: Any,
        message: Any,
        _channel: Any = None,
        _context: Any = None,
    ) -> None:
        captured["text"] = message.text
        captured["thread_id"] = getattr(thread, "id", None)
        # Author-dict bug guard: attribute access must work.
        captured["author_user_name"] = message.author.user_name
        captured["author_full_name"] = message.author.full_name
        captured["author_user_id"] = message.author.user_id
        seen.set()

    bot.on_direct_message(handler)

    headers = {
        TELEGRAM_SECRET_TOKEN_HEADER: WEBHOOK_SECRET_TOKEN,
        "content-type": "application/json",
    }
    status, _resp_headers, resp_body = await bot.handle_webhook(
        "telegram",
        _direct_message_body(),
        headers,
    )
    assert status == 200
    assert resp_body == "OK"

    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["text"] == "hello from the dm"
    assert captured["author_user_name"] == "alice_tg"
    assert captured["author_full_name"] == "Alice Example"
    assert captured["author_user_id"] == "12345"
    assert captured["thread_id"] is not None


async def test_group_mention_fires_on_new_mention_via_chat_handle_webhook(
    adapter: TelegramAdapter,
) -> None:
    bot = await _build_bot(adapter)
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, _context: Any = None) -> None:
        captured["text"] = message.text
        captured["is_mention"] = message.is_mention
        captured["author_user_name"] = message.author.user_name
        seen.set()

    bot.on_new_mention(handler)

    headers = {
        TELEGRAM_SECRET_TOKEN_HEADER: WEBHOOK_SECRET_TOKEN,
        "content-type": "application/json",
    }
    status, _resp_headers, _body = await bot.handle_webhook(
        "telegram",
        _group_mention_body(),
        headers,
    )
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert f"@{BOT_USERNAME}" in captured["text"]
    assert captured["is_mention"] is True
    assert captured["author_user_name"] == "alice_tg"


async def test_invalid_secret_token_returns_401(adapter: TelegramAdapter) -> None:
    """Tampered ``x-telegram-bot-api-secret-token`` → 401."""

    status, _headers, _body = await adapter.handle_webhook(
        _direct_message_body(),
        {TELEGRAM_SECRET_TOKEN_HEADER: "wrong-secret"},
    )
    assert status == 401


async def test_initialize_stores_chat_reference(
    monkeypatch: pytest.MonkeyPatch,
    adapter: TelegramAdapter,
) -> None:
    """Confirm ``initialize(chat)`` wires ``self._chat``.

    Monkeypatch ``_telegram_fetch`` + ``_resolve_runtime_mode`` so no
    outbound HTTP is attempted.
    """

    async def _fake_fetch(method: str, _body: Any = None) -> Any:
        if method == "getMe":
            return {"id": 9999, "username": BOT_USERNAME, "is_bot": True}
        return {}

    async def _fake_mode() -> str:
        return "webhook"

    monkeypatch.setattr(adapter, "_telegram_fetch", _fake_fetch)
    monkeypatch.setattr(adapter, "_resolve_runtime_mode", _fake_mode)

    sentinel = object()
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel
