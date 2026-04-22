"""User info caching for Google Chat.

Python port of upstream ``packages/adapter-gchat/src/user-info.ts``.

Google Chat Pub/Sub messages don't include user display names, so we cache
them from direct webhook messages for later use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from chat import Logger, StateAdapter


_USER_INFO_KEY_PREFIX = "gchat:user:"
# TTL for persisted user info: 7 days.
_USER_INFO_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000


class CachedUserInfo(TypedDict, total=False):
    """Cached user info for a Google Chat user."""

    displayName: str
    email: str


class UserInfoCache:
    """Two-tier cache (in-memory + state adapter) for Google Chat user info."""

    def __init__(self, state: StateAdapter | None, logger: Logger) -> None:
        self._state = state
        self._logger = logger
        self._in_memory: dict[str, CachedUserInfo] = {}

    async def set(
        self,
        user_id: str,
        display_name: str,
        email: str | None = None,
    ) -> None:
        """Cache *user_id → (display_name, email)*.

        No-ops if ``display_name`` is empty or the literal string ``"unknown"``.
        """

        if not display_name or display_name == "unknown":
            return

        user_info: CachedUserInfo = {"displayName": display_name}
        if email is not None:
            user_info["email"] = email

        self._in_memory[user_id] = user_info

        if self._state is not None:
            await self._state.set(
                f"{_USER_INFO_KEY_PREFIX}{user_id}",
                user_info,
                _USER_INFO_CACHE_TTL_MS,
            )

    async def get(self, user_id: str) -> CachedUserInfo | None:
        """Return cached user info or ``None`` when unknown."""

        in_memory = self._in_memory.get(user_id)
        if in_memory is not None:
            return in_memory

        if self._state is None:
            return None

        from_state = await self._state.get(f"{_USER_INFO_KEY_PREFIX}{user_id}")
        if from_state:
            self._in_memory[user_id] = from_state
            return from_state  # type: ignore[no-any-return]
        return None

    async def resolve_display_name(
        self,
        user_id: str,
        provided_display_name: str | None,
        bot_user_id: str | None,
        bot_user_name: str,
    ) -> str:
        """Best-effort resolution of a user's display name.

        Precedence:

        1. ``provided_display_name`` (if not ``None`` or ``"unknown"``) — also
           cached for future lookups.
        2. ``bot_user_name`` when ``user_id == bot_user_id``.
        3. Cache lookup.
        4. Fallback derived from the user id (``users/123`` → ``User 123``).
        """

        if provided_display_name and provided_display_name != "unknown":
            try:
                await self.set(user_id, provided_display_name)
            except Exception as err:  # pragma: no cover — defensive
                self._logger.error("Failed to cache user info", {"userId": user_id, "error": err})
            return provided_display_name

        if bot_user_id and user_id == bot_user_id:
            return bot_user_name

        cached = await self.get(user_id)
        if cached and cached.get("displayName"):
            return cached["displayName"]

        return user_id.replace("users/", "User ")


__all__ = [
    "CachedUserInfo",
    "UserInfoCache",
]
