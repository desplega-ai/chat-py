"""Phase 9 — pin the intentional :class:`chat.NotImplementedError` stub.

The Telegram adapter has **one** deliberate stub: :meth:`edit_message`
raises ``chat.NotImplementedError(feature="editMessage")`` when Telegram's
``editMessageText`` returns ``True`` (boolean) — which happens when the
edit succeeds without any observable change — *and* the corresponding
message has been evicted from the in-memory cache. In that situation the
adapter cannot reconstruct a fresh :class:`Message` to return to the
caller. Upstream has the same stub state. See ``docs/parity.md`` under
*Deliberate NotImplementedError stubs → chat-adapter-telegram*.

This test pins:
1. The exception type is :class:`chat.errors.NotImplementedError` (the
   chat-sdk variant), not Python's builtin.
2. ``feature == "editMessage"``.
"""

from __future__ import annotations

from typing import Any

import pytest
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat_adapter_telegram import create_telegram_adapter
from chat_adapter_telegram.adapter import TelegramAdapter


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> TelegramAdapter:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-test-token")
    return create_telegram_adapter()


async def test_edit_message_raises_chat_not_implemented_when_uncached(
    adapter: TelegramAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the ``editMessageText → True`` + cache-miss path."""

    async def _fake_fetch(method: str, _body: Any = None) -> Any:
        assert method == "editMessageText"
        # Telegram returns boolean ``True`` when the edit succeeds without
        # any observable change.
        return True

    monkeypatch.setattr(adapter, "_telegram_fetch", _fake_fetch)
    # Ensure the cache is empty so the stub branch is hit.
    adapter._message_cache.clear()

    with pytest.raises(ChatNotImplementedError) as excinfo:
        await adapter.edit_message(
            thread_id="telegram:12345",
            message_id="12345:99",
            message="new body",
        )

    assert excinfo.value.feature == "editMessage"
    assert excinfo.value.code == "NOT_IMPLEMENTED"
