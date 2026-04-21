"""Tests for :mod:`chat.postable_object`.

Upstream does not ship a dedicated ``postable-object.test.ts`` file, so this
test file exercises the public contract (``POSTABLE_OBJECT`` tagging,
``is_postable_object``, and the :func:`post_postable_object` dispatch).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat.postable_object import (
    POSTABLE_OBJECT,
    PostableObject,
    is_postable_object,
    post_postable_object,
)


class _Plan:
    """Minimal PostableObject-like test double."""

    type_tag = POSTABLE_OBJECT
    kind = "plan"

    def __init__(self, supported: bool = True) -> None:
        self._supported = supported
        self.posted_with: Any = None

    def get_fallback_text(self) -> str:
        return "Plan fallback"

    def get_post_data(self) -> dict[str, Any]:
        return {"tasks": ["a", "b"]}

    def is_supported(self, adapter: Any) -> bool:
        return self._supported

    def on_posted(self, context: Any) -> None:
        self.posted_with = context


class TestIsPostableObject:
    def test_tagged_object_is_postable(self) -> None:
        plan = _Plan()
        assert is_postable_object(plan) is True

    def test_untagged_object_is_not_postable(self) -> None:
        class Other:
            type_tag = object()

        assert is_postable_object(Other()) is False

    def test_plain_dict_is_not_postable(self) -> None:
        assert is_postable_object({"kind": "plan"}) is False

    def test_none_is_not_postable(self) -> None:
        assert is_postable_object(None) is False

    def test_protocol_check(self) -> None:
        plan = _Plan()
        assert isinstance(plan, PostableObject)


class TestPostPostableObject:
    async def test_uses_native_post_object_when_supported(self) -> None:
        plan = _Plan(supported=True)
        adapter = MagicMock()
        adapter.post_object = AsyncMock(return_value={"id": "m1", "threadId": "slack:C1:123"})
        post_fn = AsyncMock()

        await post_postable_object(plan, adapter, "slack:C1:t-fallback", post_fn)

        adapter.post_object.assert_awaited_once_with(
            "slack:C1:t-fallback", "plan", {"tasks": ["a", "b"]}
        )
        post_fn.assert_not_awaited()
        assert plan.posted_with is not None
        assert plan.posted_with.message_id == "m1"
        assert plan.posted_with.thread_id == "slack:C1:123"

    async def test_falls_back_when_not_supported(self) -> None:
        plan = _Plan(supported=False)
        adapter = MagicMock()
        adapter.post_object = AsyncMock()
        post_fn = AsyncMock(return_value={"id": "m2"})

        await post_postable_object(plan, adapter, "slack:C1:T", post_fn)

        post_fn.assert_awaited_once_with("slack:C1:T", "Plan fallback")
        adapter.post_object.assert_not_awaited()
        assert plan.posted_with is not None
        assert plan.posted_with.message_id == "m2"
        assert plan.posted_with.thread_id == "slack:C1:T"

    async def test_falls_back_when_adapter_has_no_post_object(self) -> None:
        plan = _Plan(supported=True)

        class _NoPostObject:
            pass

        adapter: Any = _NoPostObject()
        post_fn = AsyncMock(return_value={"id": "m3"})

        await post_postable_object(plan, adapter, "slack:C1:T", post_fn)

        post_fn.assert_awaited_once_with("slack:C1:T", "Plan fallback")

    async def test_logger_propagates_to_context(self) -> None:
        plan = _Plan(supported=False)
        adapter = MagicMock()
        post_fn = AsyncMock(return_value={"id": "m"})
        logger = MagicMock()

        await post_postable_object(plan, adapter, "t", post_fn, logger=logger)

        assert plan.posted_with.logger is logger


@pytest.fixture(autouse=True)
def _reset_mocks() -> None:
    """Pytest-asyncio needs a no-op fixture at module scope to initialise."""
    return None
