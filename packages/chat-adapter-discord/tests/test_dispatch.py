"""Phase 4 dispatch tests for :class:`DiscordAdapter`.

Pins that :meth:`chat.Chat.handle_webhook` round-trips a Discord
``INTERACTION_CREATE`` payload all the way to a registered
:func:`on_slash_command` handler, proving ``initialize(chat)`` wires
``self._chat`` correctly for the shared dispatch surface.

Signature verification is monkeypatched — the real ``verify_discord_signature``
function is covered by the unit tests in ``test_adapter.py`` and this file
focuses on the inbound dispatch wiring.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_discord import create_discord_adapter
from chat_adapter_discord.adapter import DiscordAdapter

PUBLIC_KEY_HEX = "0" * 64


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> DiscordAdapter:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", PUBLIC_KEY_HEX)
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789012345678")
    # Bypass Ed25519 verification — real signing is exercised by unit tests.
    monkeypatch.setattr(
        "chat_adapter_discord.adapter.verify_discord_signature",
        lambda *_args, **_kwargs: True,
    )
    return create_discord_adapter()


async def test_slash_command_fires_handler_via_chat_handle_webhook(
    adapter: DiscordAdapter,
) -> None:
    """Round-trip: INTERACTION_CREATE → Chat.handle_webhook → on_slash_command."""

    bot = Chat(user_name="bot", adapters={"discord": adapter}, state=create_mock_state())
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(event: dict[str, Any]) -> None:
        captured["command"] = event.get("command")
        captured["text"] = event.get("text")
        seen.set()

    bot.on_slash_command("/echo", handler)

    # APPLICATION_COMMAND interaction shape (type=2).
    payload = {
        "type": 2,
        "id": "interaction-id-1",
        "token": "interaction-token-1",
        "application_id": "123456789012345678",
        "channel_id": "channel-1",
        "guild_id": "guild-1",
        "channel": {"id": "channel-1", "type": 0},
        "data": {
            "name": "echo",
            "options": [{"name": "text", "value": "hi there"}],
        },
        "member": {
            "user": {
                "id": "user-1",
                "username": "alice",
                "global_name": "Alice",
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-signature-ed25519": "00" * 64,
        "x-signature-timestamp": "1700000000",
        "content-type": "application/json",
    }

    status, resp_headers, resp_body = await bot.handle_webhook("discord", body, headers)
    assert status == 200
    assert resp_headers.get("content-type") == "application/json"
    # Discord's ACK shape — deferred channel message.
    assert json.loads(resp_body) == {"type": 5}

    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert captured["command"] == "/echo"
    assert captured["text"] == "hi there"


async def test_initialize_stores_chat_reference(adapter: DiscordAdapter) -> None:
    sentinel = object()
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel
