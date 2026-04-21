"""Core type definitions for chat-sdk.

Python port of upstream ``packages/chat/src/types.ts``. This module is the
single home for all type aliases, ``TypedDict`` serialized payloads, and
``Protocol`` adapter interfaces used by the rest of the package.

The port is incremental: types get filled in as the modules that need them
are ported. Each section mirrors the same section in upstream so diffs stay
readable.
"""

from __future__ import annotations

from typing import (
    Any,
    Literal,
    Protocol,
    runtime_checkable,
)

# ============================================================================
# Channel Visibility
# ============================================================================

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


# Placeholder buckets for the rest of the upstream ``types.ts`` file. These
# will be filled in as modules that need them are ported. Exposing them as
# ``Any`` means downstream code can still annotate against the names without
# the type-checker blowing up on missing definitions.
Adapter = Any
AdapterPostableMessage = Any
Attachment = Any
Author = Any
Channel = Any
ChannelInfo = Any
ChatConfig = Any
ChatInstance = Any
FormattedContent = Any
LinkPreview = Any
Lock = Any
LockScope = Literal["thread", "channel"]
MessageMetadata = Any
Postable = Any
PostableAst = Any
PostableCard = Any
PostableMarkdown = Any
PostableMessage = Any
PostableRaw = Any
RawMessage = Any
StateAdapter = Any
Thread = Any
ThreadInfo = Any
ThreadSummary = Any


# ============================================================================
# Constants
# ============================================================================

THREAD_STATE_TTL_MS: int = 30 * 24 * 60 * 60 * 1000
"""Default TTL (in ms) for thread-scoped state entries. 30 days."""
