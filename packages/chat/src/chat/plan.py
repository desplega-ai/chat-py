"""Plan :class:`PostableObject` — Python port of ``packages/chat/src/plan.ts``.

A :class:`Plan` represents a live task list that can be posted to a thread and
updated in place. Adapters with native plan rendering (``post_object`` +
``edit_object``) receive the structured ``PlanModel``; others receive the
emoji-decorated fallback text via ``edit_message``.

Usage::

    plan = Plan(initial_message="Starting task...")
    await thread.post(plan)
    await plan.add_task(title="Fetch data")
    await plan.update_task("Got 42 results")
    await plan.complete(complete_message="Done!")
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from chat.markdown import parse_markdown, to_plain_text
from chat.postable_object import POSTABLE_OBJECT, PostableObjectContext

if TYPE_CHECKING:
    from chat.logger import Logger


PlanTaskStatus = Literal["pending", "in_progress", "complete", "error"]


@dataclass(slots=True)
class PlanTask:
    """Public read-only view of a single task inside a plan."""

    id: str
    title: str
    status: PlanTaskStatus


#: Accepted shape for any user-supplied plan content:
#:
#: - plain ``str``
#: - ``list[str]`` (joined with spaces)
#: - ``{"markdown": str}`` dict
#: - ``{"ast": <mdast-root>}`` dict
PlanContent = str | list[str] | dict[str, Any]


@dataclass(slots=True)
class PlanModelTask:
    """Internal task record; includes mutable details/output blobs."""

    id: str
    title: str
    status: PlanTaskStatus
    details: PlanContent | None = None
    output: PlanContent | None = None


@dataclass(slots=True)
class PlanModel:
    """Serializable wire model for a plan — handed to adapter ``post_object``."""

    title: str
    tasks: list[PlanModelTask] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_to_plain_text(content: PlanContent | None) -> str:
    """Coerce any :data:`PlanContent` to plain text for titles and fallback text."""
    if content is None:
        return ""
    if isinstance(content, list):
        return " ".join(content).strip()
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "markdown" in content:
            return to_plain_text(parse_markdown(content["markdown"]))
        if "ast" in content:
            return to_plain_text(content["ast"])
    return ""


@dataclass(slots=True)
class _BoundState:
    adapter: Any
    message_id: str
    thread_id: str
    fallback: bool
    logger: Logger | None
    update_chain: asyncio.Future[None]


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class Plan:
    """Interactive task-list :class:`~chat.postable_object.PostableObject`."""

    # Sentinel tag — identifies instances via :func:`is_postable_object`.
    type_tag: Any = POSTABLE_OBJECT
    kind: str = "plan"

    __slots__ = ("_bound", "_model")

    def __init__(self, *, initial_message: PlanContent) -> None:
        title = _content_to_plain_text(initial_message) or "Plan"
        first_task = PlanModelTask(
            id=str(uuid.uuid4()),
            title=title,
            status="in_progress",
        )
        self._model = PlanModel(title=title, tasks=[first_task])
        self._bound: _BoundState | None = None

    # ------------------------------------------------------------------
    # PostableObject protocol
    # ------------------------------------------------------------------

    def is_supported(self, adapter: Any) -> bool:
        return (
            getattr(adapter, "post_object", None) is not None
            and getattr(adapter, "edit_object", None) is not None
        )

    def get_post_data(self) -> PlanModel:
        return self._model

    def get_fallback_text(self) -> str:
        lines: list[str] = []
        lines.append(f"📋 {self._model.title or 'Plan'}")
        status_icons: dict[str, str] = {
            "complete": "✅",
            "in_progress": "🔄",
            "error": "❌",
        }
        for task in self._model.tasks:
            icon = status_icons.get(task.status, "⬜")
            lines.append(f"{icon} {task.title}")
        return "\n".join(lines)

    def on_posted(self, context: PostableObjectContext) -> None:
        loop = asyncio.get_running_loop()
        resolved: asyncio.Future[None] = loop.create_future()
        resolved.set_result(None)
        self._bound = _BoundState(
            adapter=context.adapter,
            message_id=context.message_id,
            thread_id=context.thread_id,
            fallback=not self.is_supported(context.adapter),
            logger=context.logger,
            update_chain=resolved,
        )

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._bound.message_id if self._bound else ""

    @property
    def thread_id(self) -> str:
        return self._bound.thread_id if self._bound else ""

    @property
    def title(self) -> str:
        return self._model.title

    @property
    def tasks(self) -> list[PlanTask]:
        return [PlanTask(id=t.id, title=t.title, status=t.status) for t in self._model.tasks]

    @property
    def current_task(self) -> PlanTask | None:
        current: PlanModelTask | None = None
        for t in reversed(self._model.tasks):
            if t.status == "in_progress":
                current = t
                break
        if current is None and self._model.tasks:
            current = self._model.tasks[-1]
        if current is None:
            return None
        return PlanTask(id=current.id, title=current.title, status=current.status)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def add_task(
        self,
        *,
        title: PlanContent,
        children: PlanContent | None = None,
    ) -> PlanTask | None:
        if not self._can_mutate():
            return None
        new_title = _content_to_plain_text(title) or "Task"
        for task in self._model.tasks:
            if task.status == "in_progress":
                task.status = "complete"
        next_task = PlanModelTask(
            id=str(uuid.uuid4()),
            title=new_title,
            status="in_progress",
            details=children,
        )
        self._model.tasks.append(next_task)
        self._model.title = new_title
        await self._enqueue_edit()
        return PlanTask(id=next_task.id, title=next_task.title, status=next_task.status)

    async def update_task(
        self,
        update: PlanContent | dict[str, Any] | None = None,
    ) -> PlanTask | None:
        if not self._can_mutate():
            return None
        current: PlanModelTask | None = None
        for t in reversed(self._model.tasks):
            if t.status == "in_progress":
                current = t
                break
        if current is None and self._model.tasks:
            current = self._model.tasks[-1]
        if current is None:
            return None

        if update is not None:
            if (
                isinstance(update, dict)
                and ("output" in update or "status" in update)
                and not ("markdown" in update or "ast" in update)
            ):
                if update.get("output") is not None:
                    current.output = update["output"]
                if update.get("status"):
                    current.status = update["status"]
            else:
                current.output = update  # type: ignore[assignment]

        await self._enqueue_edit()
        return PlanTask(id=current.id, title=current.title, status=current.status)

    async def reset(self, *, initial_message: PlanContent) -> PlanTask | None:
        if not self._can_mutate():
            return None
        title = _content_to_plain_text(initial_message) or "Plan"
        first_task = PlanModelTask(
            id=str(uuid.uuid4()),
            title=title,
            status="in_progress",
        )
        self._model = PlanModel(title=title, tasks=[first_task])
        await self._enqueue_edit()
        return PlanTask(id=first_task.id, title=first_task.title, status=first_task.status)

    async def complete(self, *, complete_message: PlanContent) -> None:
        if not self._can_mutate():
            return
        for task in self._model.tasks:
            if task.status == "in_progress":
                task.status = "complete"
        new_title = _content_to_plain_text(complete_message) or self._model.title
        self._model.title = new_title
        await self._enqueue_edit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _can_mutate(self) -> bool:
        return self._bound is not None

    async def _enqueue_edit(self) -> None:
        """Serialize edits through a single-slot in-order queue.

        Matches upstream's ``updateChain.then(doEdit, doEdit)`` pattern: each
        call chains behind the previous one so adapter edits fire strictly in
        the order the mutations were made, even under concurrent callers.
        """
        bound = self._bound
        if bound is None:
            return

        prev = bound.update_chain
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()

        async def runner() -> None:
            with contextlib.suppress(BaseException):
                await asyncio.shield(prev)
            try:
                if bound.fallback:
                    await bound.adapter.edit_message(
                        bound.thread_id,
                        bound.message_id,
                        self.get_fallback_text(),
                    )
                else:
                    edit_object = getattr(bound.adapter, "edit_object", None)
                    if edit_object is None:
                        done.set_result(None)
                        return
                    await edit_object(
                        bound.thread_id,
                        bound.message_id,
                        self.kind,
                        self._model,
                    )
            except BaseException as err:
                if bound.logger is not None:
                    bound.logger.warn("Failed to edit plan", err)
                if not done.done():
                    done.set_exception(err)
                return
            if not done.done():
                done.set_result(None)

        task = asyncio.create_task(runner())
        # ``done`` completes when our edit finishes, so the next enqueued edit
        # awaits it before running — preserving strict order.
        bound.update_chain = done
        try:
            await task
        finally:
            # Ensure any in-flight failure propagates to the caller of this
            # method (matches upstream: ``addTask`` await fails with edit error).
            if done.done() and done.exception() is not None:
                raise done.exception()  # type: ignore[misc]


__all__ = [
    "Plan",
    "PlanContent",
    "PlanModel",
    "PlanModelTask",
    "PlanTask",
    "PlanTaskStatus",
]
