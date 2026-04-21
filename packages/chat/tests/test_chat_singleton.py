"""Tests for :mod:`chat.chat_singleton` — port of ``chat-singleton.test.ts``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from chat.chat_singleton import (
    clear_chat_singleton,
    get_chat_singleton,
    has_chat_singleton,
    set_chat_singleton,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    clear_chat_singleton()


class TestChatSingleton:
    def test_no_singleton_by_default(self) -> None:
        assert has_chat_singleton() is False

    def test_raises_when_unregistered(self) -> None:
        with pytest.raises(RuntimeError, match="No Chat singleton registered"):
            get_chat_singleton()

    def test_set_and_get_singleton(self) -> None:
        mock = MagicMock(spec=["get_adapter", "get_state"])
        set_chat_singleton(mock)
        assert has_chat_singleton() is True
        assert get_chat_singleton() is mock

    def test_clear_singleton(self) -> None:
        mock = MagicMock(spec=["get_adapter", "get_state"])
        set_chat_singleton(mock)
        assert has_chat_singleton() is True

        clear_chat_singleton()
        assert has_chat_singleton() is False

    def test_overwrite_singleton(self) -> None:
        mock1 = MagicMock(spec=["get_adapter", "get_state"])
        mock2 = MagicMock(spec=["get_adapter", "get_state"])
        set_chat_singleton(mock1)
        set_chat_singleton(mock2)
        assert get_chat_singleton() is mock2
