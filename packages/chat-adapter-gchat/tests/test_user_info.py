"""Tests for the Google Chat user info cache.

Mirrors upstream ``packages/adapter-gchat/src/user-info.test.ts``.
"""

from __future__ import annotations

import pytest
from chat.mock_adapter import create_mock_state, mock_logger
from chat_adapter_gchat.user_info import UserInfoCache


class TestSet:
    @pytest.mark.asyncio
    async def test_stores_in_memory_and_persists_to_state(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        await cache.set("users/123", "John Doe", "john@example.com")

        assert await cache.get("users/123") == {
            "displayName": "John Doe",
            "email": "john@example.com",
        }

    @pytest.mark.asyncio
    async def test_skips_empty_display_name(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        await cache.set("users/123", "")
        assert await cache.get("users/123") is None

    @pytest.mark.asyncio
    async def test_skips_unknown_display_name(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        await cache.set("users/123", "unknown")
        assert await cache.get("users/123") is None

    @pytest.mark.asyncio
    async def test_works_without_state_adapter(self) -> None:
        cache = UserInfoCache(None, mock_logger)

        await cache.set("users/123", "John Doe")
        assert await cache.get("users/123") == {"displayName": "John Doe"}


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_from_in_memory_cache_first(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        await cache.set("users/123", "John Doe")
        # Clear state to verify in-memory is used
        state.cache.clear()

        assert await cache.get("users/123") == {"displayName": "John Doe"}

    @pytest.mark.asyncio
    async def test_falls_back_to_state_adapter(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        state.cache["gchat:user:users/456"] = {
            "displayName": "Jane",
            "email": "jane@example.com",
        }

        assert await cache.get("users/456") == {
            "displayName": "Jane",
            "email": "jane@example.com",
        }

    @pytest.mark.asyncio
    async def test_populates_in_memory_cache_on_state_hit(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        state.cache["gchat:user:users/789"] = {"displayName": "Bob"}

        await cache.get("users/789")
        state.cache.clear()

        assert await cache.get("users/789") == {"displayName": "Bob"}

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_users(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        assert await cache.get("users/unknown") is None

    @pytest.mark.asyncio
    async def test_returns_none_without_state_adapter(self) -> None:
        cache = UserInfoCache(None, mock_logger)
        assert await cache.get("users/unknown") is None


class TestResolveDisplayName:
    @pytest.mark.asyncio
    async def test_uses_provided_display_name(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        name = await cache.resolve_display_name("users/123", "John Doe", "users/bot", "chatbot")
        assert name == "John Doe"

    @pytest.mark.asyncio
    async def test_skips_unknown_provided_name(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        await cache.set("users/123", "Cached Name")

        name = await cache.resolve_display_name("users/123", "unknown", "users/bot", "chatbot")
        assert name == "Cached Name"

    @pytest.mark.asyncio
    async def test_returns_bot_name_for_bot_user_id(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        name = await cache.resolve_display_name("users/bot", None, "users/bot", "chatbot")
        assert name == "chatbot"

    @pytest.mark.asyncio
    async def test_uses_cache_for_unknown_display_name(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        await cache.set("users/456", "Cached User")

        name = await cache.resolve_display_name("users/456", None, "users/bot", "chatbot")
        assert name == "Cached User"

    @pytest.mark.asyncio
    async def test_falls_back_to_formatted_user_id(self) -> None:
        state = create_mock_state()
        cache = UserInfoCache(state, mock_logger)

        name = await cache.resolve_display_name("users/999", None, "users/bot", "chatbot")
        assert name == "User 999"
