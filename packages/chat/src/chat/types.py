"""Core type definitions for chat-sdk.

Python port of upstream ``packages/chat/src/types.ts``. This module is the
single home for all type aliases, ``TypedDict`` serialized payloads, and
``Protocol`` adapter interfaces used by the rest of the package.

The port is incremental: types get filled in as the modules that need them
are ported. Each section mirrors the same section in upstream so diffs stay
readable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    Any,
    Literal,
    NotRequired,
    Protocol,
    Required,
    TypedDict,
    runtime_checkable,
)

# ============================================================================
# Channel Visibility
# ============================================================================

FetchDirection = Literal["backward", "forward"]
"""Pagination direction for :class:`Adapter` ``fetch_messages`` / ``fetch_channel_messages``."""


ChannelVisibility = Literal["private", "workspace", "external", "unknown"]
"""Visibility scope of a channel.

- ``"private"``: only visible to invited members (e.g., private Slack channels)
- ``"workspace"``: visible to all workspace members (e.g., public Slack channels)
- ``"external"``: shared with external organizations (e.g., Slack Connect)
- ``"unknown"``: visibility cannot be determined
"""


# ============================================================================
# Reactions / Emoji
# ============================================================================


WellKnownEmoji = Literal[
    # Reactions & Gestures
    "thumbs_up",
    "thumbs_down",
    "clap",
    "wave",
    "pray",
    "muscle",
    "ok_hand",
    "point_up",
    "point_down",
    "point_left",
    "point_right",
    "raised_hands",
    "shrug",
    "facepalm",
    # Emotions & Faces
    "heart",
    "smile",
    "laugh",
    "thinking",
    "sad",
    "cry",
    "angry",
    "love_eyes",
    "cool",
    "wink",
    "surprised",
    "worried",
    "confused",
    "neutral",
    "sleeping",
    "sick",
    "mind_blown",
    "relieved",
    "grimace",
    "rolling_eyes",
    "hug",
    "zany",
    # Status & Symbols
    "check",
    "x",
    "question",
    "exclamation",
    "warning",
    "stop",
    "info",
    "100",
    "fire",
    "star",
    "sparkles",
    "lightning",
    "boom",
    "eyes",
    # Status Indicators
    "green_circle",
    "yellow_circle",
    "red_circle",
    "blue_circle",
    "white_circle",
    "black_circle",
    # Objects & Tools
    "rocket",
    "party",
    "confetti",
    "balloon",
    "gift",
    "trophy",
    "medal",
    "lightbulb",
    "gear",
    "wrench",
    "hammer",
    "bug",
    "link",
    "lock",
    "unlock",
    "key",
    "pin",
    "memo",
    "clipboard",
    "calendar",
    "clock",
    "hourglass",
    "bell",
    "megaphone",
    "speech_bubble",
    "email",
    "inbox",
    "outbox",
    "package",
    "folder",
    "file",
    "chart_up",
    "chart_down",
    "coffee",
    "pizza",
    "beer",
    # Arrows & Directions
    "arrow_up",
    "arrow_down",
    "arrow_left",
    "arrow_right",
    "refresh",
    # Nature & Weather
    "sun",
    "cloud",
    "rain",
    "snow",
    "rainbow",
]
"""Well-known emoji that work across platforms (Slack and Google Chat)."""


class EmojiFormats(Protocol):
    """Platform-specific emoji formats for a single emoji.

    Kept as a :class:`Protocol` so plain ``dict``-like objects can be passed
    where an :class:`EmojiFormats` is expected (Python's duck typing is the
    natural equivalent of TypeScript's structural interfaces). The concrete
    storage in :data:`chat.emoji.DEFAULT_EMOJI_MAP` is a plain ``dict``.
    """

    slack: str | list[str]
    gchat: str | list[str]


# ``CustomEmojiMap`` is a module-augmentation hook in upstream. In Python we
# expose ``Emoji`` as ``str`` — the runtime does not enforce the literal union.
Emoji = str

# A mapping from emoji name to :class:`EmojiFormats`-shaped dict.
EmojiMapConfig = dict[str, "EmojiFormatsDict"]


class EmojiFormatsDict(Protocol):
    """Duck-typed ``{slack, gchat}`` dict — use this for input config dicts."""

    slack: str | list[str]
    gchat: str | list[str]


@runtime_checkable
class EmojiValue(Protocol):
    """Immutable emoji value object with object identity.

    These objects are singletons: the same emoji name always returns the same
    frozen object instance, enabling ``is`` comparison.
    """

    @property
    def name(self) -> str: ...

    def __str__(self) -> str: ...


# ============================================================================
# Message building blocks (ported from ``types.ts``)
# ============================================================================


FormattedContent = dict[str, Any]
"""Formatted message content — an mdast ``Root`` dict. Canonical form for message formatting.

Alias kept loose (``dict[str, Any]``) to avoid a circular import with
:mod:`chat.markdown`, which owns the structural ``MdastRoot`` alias.
"""


@dataclass(slots=True)
class Author:
    """Author of a chat message.

    ``is_bot`` may be ``True``/``False`` or the literal string ``"unknown"``
    when the adapter can't determine bot status.
    """

    user_id: str
    user_name: str
    full_name: str
    is_bot: bool | Literal["unknown"]
    is_me: bool


@dataclass(slots=True)
class MessageMetadata:
    """When-and-how metadata for a chat message."""

    date_sent: datetime
    edited: bool
    edited_at: datetime | None = None


AttachmentType = Literal["image", "file", "video", "audio"]


@dataclass(slots=True)
class Attachment:
    """A message attachment — image, file, video, or audio.

    ``data`` / ``fetch_data`` are runtime-only: they are stripped during
    :meth:`chat.message.Message.to_json`.
    """

    type: AttachmentType
    url: str | None = None
    name: str | None = None
    mime_type: str | None = None
    size: int | None = None
    width: int | None = None
    height: int | None = None
    data: bytes | None = None
    fetch_data: Callable[[], Awaitable[bytes]] | None = None


@dataclass(slots=True)
class LinkPreview:
    """A link found in a message, with optional unfurl metadata."""

    url: str
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    site_name: str | None = None
    fetch_message: Callable[[], Awaitable[Any]] | None = None
    """Callback returning the upstream :class:`chat.message.Message`, when the
    link points to another chat message on the same platform. Typed as
    ``Any`` to avoid a circular import.
    """


# ============================================================================
# Serialized message payloads — camelCase to match upstream JSON wire format
# ============================================================================


class SerializedAuthor(TypedDict):
    """JSON shape for :class:`Author`. Keys are camelCase to match upstream wire format."""

    userId: str
    userName: str
    fullName: str
    isBot: bool | Literal["unknown"]
    isMe: bool


class SerializedMetadata(TypedDict):
    """JSON shape for :class:`MessageMetadata`. Dates are ISO-8601 strings."""

    dateSent: str
    edited: bool
    editedAt: NotRequired[str | None]


class SerializedAttachment(TypedDict, total=False):
    """JSON shape for :class:`Attachment`. ``data``/``fetchData`` are omitted."""

    type: Required[AttachmentType]
    url: str | None
    name: str | None
    mimeType: str | None
    size: int | None
    width: int | None
    height: int | None


class SerializedLinkPreview(TypedDict, total=False):
    """JSON shape for :class:`LinkPreview`. ``fetch_message`` is omitted."""

    url: Required[str]
    title: str | None
    description: str | None
    imageUrl: str | None
    siteName: str | None


class SerializedMessage(TypedDict, total=False):
    """JSON shape for :class:`chat.message.Message`. Keys are camelCase."""

    _type: Required[Literal["chat:Message"]]
    id: Required[str]
    threadId: Required[str]
    text: Required[str]
    formatted: Required[FormattedContent]
    raw: Any
    author: Required[SerializedAuthor]
    metadata: Required[SerializedMetadata]
    attachments: Required[list[SerializedAttachment]]
    isMention: NotRequired[bool | None]
    links: NotRequired[list[SerializedLinkPreview] | None]


# ============================================================================
# MessageData — constructor input for :class:`chat.message.Message`
# ============================================================================


@dataclass(slots=True)
class MessageData:
    """Constructor input for :class:`chat.message.Message`.

    Mirrors upstream ``MessageData<TRawMessage>`` — raw is ``Any`` in Python.
    """

    id: str
    thread_id: str
    text: str
    formatted: FormattedContent
    raw: Any
    author: Author
    metadata: MessageMetadata
    attachments: list[Attachment] = field(default_factory=list)
    is_mention: bool | None = None
    links: list[LinkPreview] = field(default_factory=list)


# ============================================================================
# Concurrency — Lock and QueueEntry
# ============================================================================


@dataclass(slots=True)
class Lock:
    """A distributed lock on a thread (or channel) held by a single handler."""

    thread_id: str
    token: str
    expires_at: int
    """Expiry time as Unix milliseconds — matches upstream ``Date.now()`` semantics."""


@dataclass(slots=True)
class QueueEntry:
    """An entry in the per-thread message queue.

    Used by the ``queue`` and ``debounce`` concurrency strategies.
    """

    enqueued_at: int
    """When this entry was enqueued (Unix ms)."""

    expires_at: int
    """When this entry expires (Unix ms). Stale entries are discarded on dequeue."""

    message: Any
    """The queued :class:`chat.message.Message`. Typed ``Any`` to avoid circular import."""


# ============================================================================
# State Adapter — pluggable persistence for subscriptions, locks, and state
# ============================================================================


class AppendToListOptions(TypedDict, total=False):
    """Options for :meth:`StateAdapter.append_to_list`."""

    maxLength: int
    ttlMs: int


@runtime_checkable
class StateAdapter(Protocol):
    """Pluggable state backend — subscriptions, locks, key-value cache, queues, lists.

    All methods are async to match upstream. Implementations live in
    ``chat-adapter-state-memory`` / ``chat-adapter-state-redis`` / etc.
    """

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    # Subscriptions
    async def subscribe(self, thread_id: str) -> None: ...
    async def unsubscribe(self, thread_id: str) -> None: ...
    async def is_subscribed(self, thread_id: str) -> bool: ...

    # Locks
    async def acquire_lock(self, thread_id: str, ttl_ms: int) -> Lock | None: ...
    async def release_lock(self, lock: Lock) -> None: ...
    async def force_release_lock(self, thread_id: str) -> None: ...
    async def extend_lock(self, lock: Lock, ttl_ms: int) -> bool: ...

    # Key-value cache
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any, ttl_ms: int | None = None) -> None: ...
    async def set_if_not_exists(self, key: str, value: Any, ttl_ms: int | None = None) -> bool: ...
    async def delete(self, key: str) -> None: ...

    # Lists
    async def append_to_list(
        self, key: str, value: Any, options: AppendToListOptions | None = None
    ) -> None: ...
    async def get_list(self, key: str) -> list[Any]: ...

    # Queues
    async def enqueue(self, thread_id: str, entry: QueueEntry, max_size: int) -> int: ...
    async def dequeue(self, thread_id: str) -> QueueEntry | None: ...
    async def queue_depth(self, thread_id: str) -> int: ...


# ============================================================================
# Channel metadata / thread summary / pagination (ported from ``types.ts``)
# ============================================================================


class ChannelInfo(TypedDict, total=False):
    """Channel metadata returned by :meth:`Adapter.fetch_channel_info`.

    Keys use camelCase to match the upstream wire shape (``isDM``,
    ``memberCount``, etc.).
    """

    id: Required[str]
    isDM: bool
    memberCount: int
    metadata: Required[dict[str, Any]]
    name: str
    channelVisibility: ChannelVisibility


class ThreadSummary(TypedDict, total=False):
    """Lightweight summary of a thread within a channel."""

    id: Required[str]
    lastReplyAt: datetime
    replyCount: int
    rootMessage: Required[Any]
    """The :class:`chat.message.Message` — typed ``Any`` to avoid circular import."""


class ListThreadsOptions(TypedDict, total=False):
    """Options for :meth:`Adapter.list_threads`."""

    cursor: str
    limit: int


class ListThreadsResult(TypedDict, total=False):
    """Result of :meth:`Adapter.list_threads`."""

    nextCursor: str | None
    threads: Required[list[ThreadSummary]]


class FetchOptions(TypedDict, total=False):
    """Options for :meth:`Adapter.fetch_messages` / ``fetch_channel_messages``."""

    cursor: str
    direction: FetchDirection
    limit: int


class FetchResult(TypedDict, total=False):
    """Result of :meth:`Adapter.fetch_messages`."""

    messages: Required[list[Any]]
    """Messages in chronological order (oldest first within this page)."""
    nextCursor: str | None


# ============================================================================
# Raw / Postable message shapes
# ============================================================================


class RawMessage(TypedDict, total=False):
    """Raw message returned from adapter ``post_message`` / ``edit_message``."""

    id: Required[str]
    raw: Any
    threadId: str | None


class PostableRaw(TypedDict, total=False):
    """Raw-text postable: :attr:`raw` bypasses platform formatting."""

    raw: Required[str]
    attachments: list[Attachment | dict[str, Any]]
    files: list[Any]


class PostableMarkdown(TypedDict, total=False):
    """Markdown postable — converted to platform format."""

    markdown: Required[str]
    attachments: list[Attachment | dict[str, Any]]
    files: list[Any]


class PostableAst(TypedDict, total=False):
    """mdast AST postable — converted to platform format."""

    ast: Required[FormattedContent]
    attachments: list[Attachment | dict[str, Any]]
    files: list[Any]


AdapterPostableMessage = str | PostableRaw | PostableMarkdown | PostableAst | dict[str, Any]
"""Postable message accepted by adapter-level ``post_message``.

Excludes streams — the adapter handles content synchronously. Card / JSX
variants are part B of the port.
"""

PostableMessage = AdapterPostableMessage | Any
"""Postable at the :class:`Thread` / :class:`Channel` level — includes
streams and :class:`chat.postable_object.PostableObject`. Loosely typed as
``Any`` because ``AsyncIterable[str]`` and ``PostableObject`` don't flatten
into a clean union.
"""


# ============================================================================
# Stream chunks — structured events a stream can yield to ``thread.post``
# ============================================================================


class MarkdownTextChunk(TypedDict):
    """Markdown text chunk — yielded by streaming renderers."""

    type: Literal["markdown_text"]
    text: str


class TaskUpdateChunk(TypedDict, total=False):
    """Tool/step progress card — ``pending → in_progress → complete → error``."""

    type: Required[Literal["task_update"]]
    id: Required[str]
    title: Required[str]
    status: Required[Literal["pending", "in_progress", "complete", "error"]]
    output: str


class PlanUpdateChunk(TypedDict):
    """Plan title update."""

    type: Literal["plan_update"]
    title: str


StreamChunk = MarkdownTextChunk | TaskUpdateChunk | PlanUpdateChunk
"""Structured chunk type — adapters without native support extract ``text`` from markdown chunks."""


# ============================================================================
# Sent / Ephemeral / Scheduled messages
# ============================================================================


class PostEphemeralOptions(TypedDict):
    """Options for :meth:`Channel.post_ephemeral` / :meth:`Thread.post_ephemeral`."""

    fallbackToDM: bool


@dataclass(slots=True)
class EphemeralMessage:
    """Result of posting an ephemeral message.

    Ephemeral messages are visible only to a specific user and typically
    cannot be edited or deleted.
    """

    id: str
    thread_id: str
    used_fallback: bool
    raw: Any


@dataclass(slots=True)
class ScheduledMessage:
    """Result of scheduling a message for future delivery.

    Only supported by adapters with native scheduling APIs (e.g., Slack).
    """

    scheduled_message_id: str
    channel_id: str
    post_at: datetime
    raw: Any
    cancel: Callable[[], Awaitable[None]]
    """Cancel the scheduled message before it's sent."""


# ============================================================================
# Placeholder buckets for remaining ``types.ts`` types
#
# These are filled in as their consuming modules are ported. Aliased to
# ``Any`` so downstream code can annotate against names without mypy blowing
# up on missing definitions.
# ============================================================================

Adapter = Any
Channel = Any
ChatConfig = Any
ChatInstance = Any
LockScope = Literal["thread", "channel"]
Postable = Any
PostableCard = Any
Thread = Any
ThreadInfo = Any


# ============================================================================
# Constants
# ============================================================================

THREAD_STATE_TTL_MS: int = 30 * 24 * 60 * 60 * 1000
"""Default TTL (in ms) for thread-scoped state entries. 30 days."""
