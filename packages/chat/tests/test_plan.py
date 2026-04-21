"""Tests for :mod:`chat.plan` — mirrors upstream plan tests in ``thread.test.ts``."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat.plan import Plan, PlanTask, _content_to_plain_text
from chat.postable_object import SimplePostableObjectContext, is_postable_object


@pytest.fixture
def mock_adapter_native() -> MagicMock:
    """Adapter with native ``post_object`` + ``edit_object`` support."""
    adapter = MagicMock()
    adapter.post_object = AsyncMock(
        return_value={"id": "plan-msg-1", "threadId": "slack:C123:1234.5678"}
    )
    adapter.edit_object = AsyncMock(return_value=None)
    adapter.post_message = AsyncMock(return_value={"id": "msg-1"})
    adapter.edit_message = AsyncMock(return_value=None)
    return adapter


@pytest.fixture
def mock_adapter_fallback() -> MagicMock:
    """Adapter without ``post_object``/``edit_object`` — fallback mode."""
    adapter = MagicMock(spec=["post_message", "edit_message"])
    adapter.post_message = AsyncMock(return_value={"id": "msg-1"})
    adapter.edit_message = AsyncMock(return_value=None)
    return adapter


async def _bind_plan(plan: Plan, adapter: Any, message_id: str = "msg-1") -> None:
    """Simulate :meth:`Plan.on_posted` without going through ``thread.post``."""
    plan.on_posted(
        SimplePostableObjectContext(
            adapter=adapter,
            thread_id="slack:C123:1234.5678",
            message_id=message_id,
        )
    )


# ---------------------------------------------------------------------------
# Type guard / PostableObject integration
# ---------------------------------------------------------------------------


class TestPostableObjectProtocol:
    def test_is_postable_object(self) -> None:
        plan = Plan(initial_message="Start")
        assert is_postable_object(plan) is True

    def test_kind_is_plan(self) -> None:
        assert Plan.kind == "plan"

    def test_is_supported_requires_both_post_and_edit(self) -> None:
        plan = Plan(initial_message="Start")
        full = MagicMock()
        full.post_object = AsyncMock()
        full.edit_object = AsyncMock()
        assert plan.is_supported(full) is True

        partial = MagicMock(spec=["post_object"])
        partial.post_object = AsyncMock()
        assert plan.is_supported(partial) is False


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_sets_initial_title_from_string(self) -> None:
        plan = Plan(initial_message="Starting...")
        assert plan.title == "Starting..."
        assert len(plan.tasks) == 1
        assert plan.tasks[0].status == "in_progress"

    def test_empty_initial_message_defaults_to_plan(self) -> None:
        plan = Plan(initial_message="")
        assert plan.title == "Plan"

    def test_list_initial_message_joins_with_spaces(self) -> None:
        plan = Plan(initial_message=["Line 1", "Line 2"])
        assert plan.title == "Line 1 Line 2"

    def test_markdown_initial_message(self) -> None:
        plan = Plan(initial_message={"markdown": "**Bold** text"})
        assert plan.title == "Bold text"

    def test_id_and_thread_id_empty_before_post(self) -> None:
        plan = Plan(initial_message="Start")
        assert plan.id == ""
        assert plan.thread_id == ""


# ---------------------------------------------------------------------------
# Fallback text
# ---------------------------------------------------------------------------


class TestFallbackText:
    def test_renders_title_and_first_task(self) -> None:
        plan = Plan(initial_message="Starting task...")
        text = plan.get_fallback_text()
        assert "📋 Starting task..." in text
        assert "🔄 Starting task..." in text

    def test_renders_status_icons(self) -> None:
        plan = Plan(initial_message="Start")
        plan._model.tasks[0].status = "complete"
        plan._model.tasks.append(
            plan._model.tasks[0].__class__(id="t2", title="Task 2", status="error")
        )
        text = plan.get_fallback_text()
        assert "✅" in text
        assert "❌" in text


# ---------------------------------------------------------------------------
# Fallback mode (adapter lacks post_object)
# ---------------------------------------------------------------------------


class TestFallbackMode:
    async def test_add_task_edits_via_edit_message(self, mock_adapter_fallback: MagicMock) -> None:
        plan = Plan(initial_message="Starting...")
        await _bind_plan(plan, mock_adapter_fallback)

        task = await plan.add_task(title="Task 1")
        assert task is not None
        assert task.title == "Task 1"

        mock_adapter_fallback.edit_message.assert_awaited()
        call_args = mock_adapter_fallback.edit_message.await_args_list[-1].args
        assert call_args[0] == "slack:C123:1234.5678"
        assert call_args[1] == "msg-1"
        assert "Task 1" in call_args[2]

    async def test_complete_marks_all_tasks_done_and_updates_title(
        self, mock_adapter_fallback: MagicMock
    ) -> None:
        plan = Plan(initial_message="Starting...")
        await _bind_plan(plan, mock_adapter_fallback)
        await plan.add_task(title="Step 1")
        await plan.complete(complete_message="All done!")

        assert plan.title == "All done!"
        for task in plan.tasks:
            assert task.status == "complete"

        last_call = mock_adapter_fallback.edit_message.await_args_list[-1].args
        assert "✅" in last_call[2]


# ---------------------------------------------------------------------------
# Native mode (adapter has post_object / edit_object)
# ---------------------------------------------------------------------------


class TestNativeMode:
    async def test_add_task_calls_edit_object(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="Starting")
        await _bind_plan(plan, mock_adapter_native, message_id="plan-msg-1")

        task = await plan.add_task(title="Fetch data", children=["Call API", "Parse"])
        assert task is not None
        assert task.title == "Fetch data"
        assert task.status == "in_progress"

        mock_adapter_native.edit_object.assert_awaited()
        assert plan.title == "Fetch data"
        assert len(plan.tasks) == 2

    async def test_update_task_with_output_string(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="Working")
        await _bind_plan(plan, mock_adapter_native)
        await plan.add_task(title="Step 1")

        result = await plan.update_task("Got result: 42")
        assert result is not None

    async def test_update_task_with_error_status(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="Start")
        await _bind_plan(plan, mock_adapter_native)
        await plan.add_task(title="Risky step")

        await plan.update_task({"status": "error", "output": "Something failed"})
        current = plan.current_task
        assert current is not None
        assert current.status == "error"

    async def test_complete_marks_all_tasks(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="Starting")
        await _bind_plan(plan, mock_adapter_native)
        await plan.add_task(title="Task 1")
        await plan.complete(complete_message="All done!")

        assert plan.title == "All done!"
        for t in plan.tasks:
            assert t.status == "complete"

    async def test_reset_replaces_model(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="First run")
        await _bind_plan(plan, mock_adapter_native)
        await plan.add_task(title="Task A")
        await plan.add_task(title="Task B")

        assert len(plan.tasks) == 3

        new_task = await plan.reset(initial_message="Second run")
        assert new_task is not None
        assert plan.title == "Second run"
        assert len(plan.tasks) == 1
        assert plan.tasks[0].status == "in_progress"


# ---------------------------------------------------------------------------
# current_task property
# ---------------------------------------------------------------------------


class TestCurrentTask:
    async def test_returns_first_in_progress_task(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="Start")
        await _bind_plan(plan, mock_adapter_native)

        current = plan.current_task
        assert current is not None
        assert current.title == "Start"
        assert current.status == "in_progress"

        await plan.add_task(title="Step 2")
        current = plan.current_task
        assert current is not None
        assert current.title == "Step 2"
        assert current.status == "in_progress"

    async def test_returns_last_task_after_complete(self, mock_adapter_native: MagicMock) -> None:
        plan = Plan(initial_message="Start")
        await _bind_plan(plan, mock_adapter_native)
        await plan.add_task(title="Step 2")
        await plan.complete(complete_message="Done")

        current = plan.current_task
        assert current is not None
        assert current.title == "Step 2"
        assert current.status == "complete"


# ---------------------------------------------------------------------------
# Unposted-plan safety
# ---------------------------------------------------------------------------


class TestUnposted:
    async def test_add_task_returns_none_before_post(self) -> None:
        plan = Plan(initial_message="Not posted yet")
        assert await plan.add_task(title="Task 1") is None

    async def test_update_task_returns_none_before_post(self) -> None:
        plan = Plan(initial_message="Not posted yet")
        assert await plan.update_task("some output") is None

    async def test_complete_no_op_before_post(self) -> None:
        plan = Plan(initial_message="Not posted yet")
        await plan.complete(complete_message="Done")
        assert plan.tasks[0].status == "in_progress"


# ---------------------------------------------------------------------------
# Error propagation and update-chain ordering
# ---------------------------------------------------------------------------


class TestUpdateChain:
    async def test_propagates_edit_object_error_from_add_task(
        self, mock_adapter_native: MagicMock
    ) -> None:
        mock_adapter_native.edit_object = AsyncMock(side_effect=RuntimeError("rate limited"))
        plan = Plan(initial_message="Start")
        await _bind_plan(plan, mock_adapter_native)

        with pytest.raises(RuntimeError, match="rate limited"):
            await plan.add_task(title="Task 1")

        assert len(plan.tasks) == 2  # model mutated even though edit failed

    async def test_continues_after_failed_edit(self, mock_adapter_native: MagicMock) -> None:
        call_count = 0

        async def flaky(*_args: Any, **_kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("rate limited")
            return None

        mock_adapter_native.edit_object = AsyncMock(side_effect=flaky)

        plan = Plan(initial_message="Start")
        await _bind_plan(plan, mock_adapter_native)

        with pytest.raises(RuntimeError):
            await plan.add_task(title="Task 1")

        await plan.add_task(title="Task 2")
        assert len(plan.tasks) == 3
        assert call_count == 2

    async def test_concurrent_edits_run_in_order(self, mock_adapter_native: MagicMock) -> None:
        """Concurrent mutation calls must still invoke ``edit_object`` in FIFO order."""
        edit_order: list[int] = []
        edit_count = 0

        async def ordered_edit(*_args: Any, **_kwargs: Any) -> None:
            nonlocal edit_count
            edit_count += 1
            my_n = edit_count
            # Simulate random-ish async delay without randomness for reproducibility.
            await asyncio.sleep(0.005 if my_n == 1 else 0.001)
            edit_order.append(my_n)

        mock_adapter_native.edit_object = AsyncMock(side_effect=ordered_edit)

        plan = Plan(initial_message="Start")
        await _bind_plan(plan, mock_adapter_native)

        await asyncio.gather(
            plan.add_task(title="Task 1"),
            plan.update_task("Output 1"),
            plan.add_task(title="Task 2"),
        )

        assert edit_order == [1, 2, 3]


# ---------------------------------------------------------------------------
# _content_to_plain_text helper
# ---------------------------------------------------------------------------


class TestContentToPlainText:
    def test_string(self) -> None:
        assert _content_to_plain_text("hello") == "hello"

    def test_list(self) -> None:
        assert _content_to_plain_text(["a", "b", "c"]) == "a b c"

    def test_markdown_dict(self) -> None:
        assert _content_to_plain_text({"markdown": "**Bold**"}) == "Bold"

    def test_ast_dict(self) -> None:
        from chat.markdown import paragraph, root, text

        ast = root([paragraph([text("hi")])])
        assert _content_to_plain_text({"ast": ast}) == "hi"

    def test_none(self) -> None:
        assert _content_to_plain_text(None) == ""


# ---------------------------------------------------------------------------
# PlanTask dataclass
# ---------------------------------------------------------------------------


def test_plan_task_dataclass() -> None:
    task = PlanTask(id="abc", title="Task 1", status="in_progress")
    assert task.id == "abc"
    assert task.title == "Task 1"
    assert task.status == "in_progress"
