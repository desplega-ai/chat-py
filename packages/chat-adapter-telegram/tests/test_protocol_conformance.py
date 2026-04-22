"""Telegram adapter structural conformance against ``chat.types.Adapter``.

Part of DES-196 Phase 9. Ensures :class:`TelegramAdapter` advertises every
method + attribute required by the shared ``Adapter`` Protocol so that
``isinstance(adapter, Adapter)`` holds and ``Chat.handle_webhook`` can
dispatch through it uniformly.

The adapter ships :meth:`TelegramAdapter.edit_message` as a *partial*
implementation that raises :class:`chat.NotImplementedError` only on the
edge case where Telegram's Bot API returns ``true`` (boolean) instead of
a full ``Message`` and the cached original has been evicted. Protocol
conformance only requires the method to *exist* with the right
signature; runtime stub behaviour is pinned in
:mod:`test_unsupported_features`.
"""

from __future__ import annotations

import pytest
from chat.types import Adapter
from chat_adapter_telegram import create_telegram_adapter


def test_telegram_adapter_implements_adapter_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-test-token")
    adapter = create_telegram_adapter()
    assert isinstance(adapter, Adapter), "TelegramAdapter missing Protocol methods"
