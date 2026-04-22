"""Phase 7 dispatch round-trip tests for :class:`TeamsAdapter`.

Exercises ``message`` Bot Framework activities through
:meth:`Chat.handle_webhook` and asserts ``on_new_mention`` fires.

JWT verification is bypassed by monkey-patching
:func:`chat_adapter_teams.adapter.verify_bearer_token` to always
return ``True`` — Bot Framework JWKS fetches would otherwise require
network access.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_teams import adapter as teams_adapter_module
from chat_adapter_teams import create_teams_adapter


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TEAMS_APP_ID", "app-id-test")
    monkeypatch.setenv("TEAMS_APP_PASSWORD", "secret")
    monkeypatch.setattr(teams_adapter_module, "verify_bearer_token", lambda *a, **kw: True)
    return create_teams_adapter()


async def test_message_activity_fires_mention_handler(adapter: Any) -> None:
    bot = Chat(user_name="bot", adapters={"teams": adapter}, state=create_mock_state())
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, context: Any = None) -> None:
        captured["text"] = message.text
        captured["thread_id"] = thread.id
        seen.set()

    bot.on_new_mention(handler)

    activity = {
        "type": "message",
        "id": "1234",
        "timestamp": "2026-04-22T12:00:00Z",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": "19:channel@thread.tacv2"},
        "from": {"id": "29:user-aad-id", "name": "Alice"},
        "text": "@bot hello there",
    }
    status, _h, _b = await bot.handle_webhook("teams", activity, {"authorization": "Bearer dummy"})
    assert status == 200
    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert "hello there" in captured["text"]
    assert captured["thread_id"].startswith("teams:")


async def test_handle_webhook_rejects_bad_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEAMS_APP_ID", "app-id-test")
    monkeypatch.setattr(teams_adapter_module, "verify_bearer_token", lambda *a, **kw: False)
    adapter = create_teams_adapter()
    status, _h, body = await adapter.handle_webhook(
        {"type": "message"}, {"authorization": "Bearer bogus"}
    )
    assert status == 401
    assert body == "unauthorized"


async def test_parse_message_uses_author_class(adapter: Any) -> None:
    """Regression: ``parse_message`` must return ``Author`` instances, not dicts."""

    from chat import Author

    activity = {
        "type": "message",
        "id": "m1",
        "timestamp": "2026-04-22T12:00:00Z",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": "19:c@thread.tacv2"},
        "from": {"id": "29:u", "name": "Alice"},
        "text": "hi",
    }
    msg = adapter.parse_message(activity)
    assert isinstance(msg.author, Author), "author must be Author, not dict"
    assert msg.author.user_name == "Alice"


__all__ = [
    "test_handle_webhook_rejects_bad_jwt",
    "test_message_activity_fires_mention_handler",
    "test_parse_message_uses_author_class",
]
