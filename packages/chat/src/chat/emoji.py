"""Emoji utilities — port of upstream ``packages/chat/src/emoji.ts``.

Provides:

- :class:`EmojiValueImpl` — the concrete immutable emoji singleton
- :func:`get_emoji` — factory that returns the same singleton for a given name
- :data:`DEFAULT_EMOJI_MAP` — the canonical emoji-name ``→`` platform-format map
- :class:`EmojiResolver` — bi-directional conversion between normalized names and platform formats
- :func:`convert_emoji_placeholders` — expand ``{{emoji:name}}`` placeholders in text
- :func:`create_emoji` / :data:`emoji` — type-safe emoji helper namespaces

The behavior matches upstream exactly: ``{{emoji:name}}`` is the placeholder
shape used for string rendering, and ``get_emoji(name)`` is idempotent so the
``is`` operator can be used for identity checks (the Python equivalent of
upstream's ``===``).
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

# ---------------------------------------------------------------------------
# EmojiValue - immutable singletons with object identity
# ---------------------------------------------------------------------------


class EmojiValueImpl:
    """Immutable singleton emoji object.

    Instances are interned via :func:`get_emoji`. The public ``name`` attribute
    is frozen; ``__str__`` and the JSON representation return the
    ``{{emoji:<name>}}`` placeholder so message rendering can round-trip.

    Instances compare ``==`` / ``is`` by identity — same name ⇒ same object.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        # Use object.__setattr__ so we can freeze attribute mutation afterwards.
        object.__setattr__(self, "_name", name)

    @property
    def name(self) -> str:
        return self._name

    def __setattr__(self, key: str, value: Any) -> None:  # pragma: no cover - defensive
        raise AttributeError(f"EmojiValue is immutable (tried to set {key!r})")

    def __str__(self) -> str:
        return f"{{{{emoji:{self._name}}}}}"

    def __repr__(self) -> str:  # pragma: no cover - debug-only
        return f"EmojiValue({self._name!r})"

    def to_json(self) -> str:
        """Return the placeholder form for JSON serialization."""
        return str(self)

    # Python's ``json`` module honours ``__json__`` on some libraries but not
    # stdlib; to keep serialization predictable we expose a top-level helper
    # rather than overriding ``__iter__`` / ``__dict__`` tricks.


_EMOJI_REGISTRY: dict[str, EmojiValueImpl] = {}


def get_emoji(name: str) -> EmojiValueImpl:
    """Return the immutable singleton for ``name``.

    Always returns the same object for the same name — enabling ``is``
    comparison for emoji identity.
    """
    existing = _EMOJI_REGISTRY.get(name)
    if existing is not None:
        return existing
    value = EmojiValueImpl(name)
    _EMOJI_REGISTRY[name] = value
    return value


# ---------------------------------------------------------------------------
# Default emoji map
# ---------------------------------------------------------------------------

# Shape: ``{normalized_name: {"slack": str | [str], "gchat": str | [str]}}``.
# Stored as plain dicts (not dataclasses) so they round-trip through JSON
# serialization exactly like upstream's plain objects.
EmojiFormatsDict = dict[str, Any]
EmojiMapConfig = dict[str, EmojiFormatsDict]


DEFAULT_EMOJI_MAP: Final[dict[str, EmojiFormatsDict]] = {
    # Reactions & Gestures
    "thumbs_up": {"slack": ["+1", "thumbsup"], "gchat": "👍"},
    "thumbs_down": {"slack": ["-1", "thumbsdown"], "gchat": "👎"},
    "clap": {"slack": "clap", "gchat": "👏"},
    "wave": {"slack": "wave", "gchat": "👋"},
    "pray": {"slack": "pray", "gchat": "🙏"},
    "muscle": {"slack": "muscle", "gchat": "💪"},
    "ok_hand": {"slack": "ok_hand", "gchat": "👌"},
    "point_up": {"slack": "point_up", "gchat": "👆"},
    "point_down": {"slack": "point_down", "gchat": "👇"},
    "point_left": {"slack": "point_left", "gchat": "👈"},
    "point_right": {"slack": "point_right", "gchat": "👉"},
    "raised_hands": {"slack": "raised_hands", "gchat": "🙌"},
    "shrug": {"slack": "shrug", "gchat": "🤷"},
    "facepalm": {"slack": "facepalm", "gchat": "🤦"},
    # Emotions & Faces
    "heart": {"slack": "heart", "gchat": ["❤️", "❤"]},
    "smile": {"slack": ["smile", "slightly_smiling_face"], "gchat": "😊"},
    "laugh": {"slack": ["laughing", "satisfied", "joy"], "gchat": ["😂", "😆"]},
    "thinking": {"slack": "thinking_face", "gchat": "🤔"},
    "sad": {"slack": ["cry", "sad", "white_frowning_face"], "gchat": "😢"},
    "cry": {"slack": "sob", "gchat": "😭"},
    "angry": {"slack": "angry", "gchat": "😠"},
    "love_eyes": {"slack": "heart_eyes", "gchat": "😍"},
    "cool": {"slack": "sunglasses", "gchat": "😎"},
    "wink": {"slack": "wink", "gchat": "😉"},
    "surprised": {"slack": "open_mouth", "gchat": "😮"},
    "worried": {"slack": "worried", "gchat": "😟"},
    "confused": {"slack": "confused", "gchat": "😕"},
    "neutral": {"slack": "neutral_face", "gchat": "😐"},
    "sleeping": {"slack": "sleeping", "gchat": "😴"},
    "sick": {"slack": "nauseated_face", "gchat": "🤢"},
    "mind_blown": {"slack": "exploding_head", "gchat": "🤯"},
    "relieved": {"slack": "relieved", "gchat": "😌"},
    "grimace": {"slack": "grimacing", "gchat": "😬"},
    "rolling_eyes": {"slack": "rolling_eyes", "gchat": "🙄"},
    "hug": {"slack": "hugging_face", "gchat": "🤗"},
    "zany": {"slack": "zany_face", "gchat": "🤪"},
    # Status & Symbols
    "check": {
        "slack": ["white_check_mark", "heavy_check_mark"],
        "gchat": ["✅", "✔️"],
    },
    "x": {"slack": ["x", "heavy_multiplication_x"], "gchat": ["❌", "✖️"]},
    "question": {"slack": "question", "gchat": ["❓", "?"]},
    "exclamation": {"slack": "exclamation", "gchat": "❗"},
    "warning": {"slack": "warning", "gchat": "⚠️"},
    "stop": {"slack": "octagonal_sign", "gchat": "🛑"},
    "info": {"slack": "information_source", "gchat": "ℹ️"},  # noqa: RUF001
    "100": {"slack": "100", "gchat": "💯"},
    "fire": {"slack": "fire", "gchat": "🔥"},
    "star": {"slack": "star", "gchat": "⭐"},
    "sparkles": {"slack": "sparkles", "gchat": "✨"},
    "lightning": {"slack": "zap", "gchat": "⚡"},
    "boom": {"slack": "boom", "gchat": "💥"},
    "eyes": {"slack": "eyes", "gchat": "👀"},
    # Status Indicators
    "green_circle": {"slack": "large_green_circle", "gchat": "🟢"},
    "yellow_circle": {"slack": "large_yellow_circle", "gchat": "🟡"},
    "red_circle": {"slack": "red_circle", "gchat": "🔴"},
    "blue_circle": {"slack": "large_blue_circle", "gchat": "🔵"},
    "white_circle": {"slack": "white_circle", "gchat": "⚪"},
    "black_circle": {"slack": "black_circle", "gchat": "⚫"},
    # Objects & Tools
    "rocket": {"slack": "rocket", "gchat": "🚀"},
    "party": {"slack": ["tada", "partying_face"], "gchat": ["🎉", "🥳"]},
    "confetti": {"slack": "confetti_ball", "gchat": "🎊"},
    "balloon": {"slack": "balloon", "gchat": "🎈"},
    "gift": {"slack": "gift", "gchat": "🎁"},
    "trophy": {"slack": "trophy", "gchat": "🏆"},
    "medal": {"slack": "first_place_medal", "gchat": "🥇"},
    "lightbulb": {"slack": "bulb", "gchat": "💡"},
    "gear": {"slack": "gear", "gchat": "⚙️"},
    "wrench": {"slack": "wrench", "gchat": "🔧"},
    "hammer": {"slack": "hammer", "gchat": "🔨"},
    "bug": {"slack": "bug", "gchat": "🐛"},
    "link": {"slack": "link", "gchat": "🔗"},
    "lock": {"slack": "lock", "gchat": "🔒"},
    "unlock": {"slack": "unlock", "gchat": "🔓"},
    "key": {"slack": "key", "gchat": "🔑"},
    "pin": {"slack": "pushpin", "gchat": "📌"},
    "memo": {"slack": "memo", "gchat": "📝"},
    "clipboard": {"slack": "clipboard", "gchat": "📋"},
    "calendar": {"slack": "calendar", "gchat": "📅"},
    "clock": {"slack": "clock1", "gchat": "🕐"},
    "hourglass": {"slack": "hourglass", "gchat": "⏳"},
    "bell": {"slack": "bell", "gchat": "🔔"},
    "megaphone": {"slack": "mega", "gchat": "📢"},
    "speech_bubble": {"slack": "speech_balloon", "gchat": "💬"},
    "email": {"slack": "email", "gchat": "📧"},
    "inbox": {"slack": "inbox_tray", "gchat": "📥"},
    "outbox": {"slack": "outbox_tray", "gchat": "📤"},
    "package": {"slack": "package", "gchat": "📦"},
    "folder": {"slack": "file_folder", "gchat": "📁"},
    "file": {"slack": "page_facing_up", "gchat": "📄"},
    "chart_up": {"slack": "chart_with_upwards_trend", "gchat": "📈"},
    "chart_down": {"slack": "chart_with_downwards_trend", "gchat": "📉"},
    "coffee": {"slack": "coffee", "gchat": "☕"},
    "pizza": {"slack": "pizza", "gchat": "🍕"},
    "beer": {"slack": "beer", "gchat": "🍺"},
    # Arrows & Directions
    "arrow_up": {"slack": "arrow_up", "gchat": "⬆️"},
    "arrow_down": {"slack": "arrow_down", "gchat": "⬇️"},
    "arrow_left": {"slack": "arrow_left", "gchat": "⬅️"},
    "arrow_right": {"slack": "arrow_right", "gchat": "➡️"},
    "refresh": {"slack": "arrows_counterclockwise", "gchat": "🔄"},
    # Nature & Weather
    "sun": {"slack": "sunny", "gchat": "☀️"},
    "cloud": {"slack": "cloud", "gchat": "☁️"},
    "rain": {"slack": "rain_cloud", "gchat": "🌧️"},
    "snow": {"slack": "snowflake", "gchat": "❄️"},
    "rainbow": {"slack": "rainbow", "gchat": "🌈"},
}


# ---------------------------------------------------------------------------
# EmojiResolver
# ---------------------------------------------------------------------------


Platform = Literal["slack", "gchat", "teams", "discord", "github", "linear", "whatsapp"]


_COLON_STRIP_RE = re.compile(r"^:|:$")
EMOJI_PLACEHOLDER_REGEX = re.compile(r"\{\{emoji:([a-z0-9_]+)\}\}", re.IGNORECASE)


def _as_list(value: str | list[str]) -> list[str]:
    return value if isinstance(value, list) else [value]


class EmojiResolver:
    """Resolve between platform-specific emoji formats and normalized names."""

    def __init__(self, custom_map: EmojiMapConfig | None = None) -> None:
        self._emoji_map: dict[str, EmojiFormatsDict] = {**DEFAULT_EMOJI_MAP}
        if custom_map:
            self._emoji_map.update(custom_map)
        self._slack_to_normalized: dict[str, str] = {}
        self._gchat_to_normalized: dict[str, str] = {}
        self._build_reverse_maps()

    def _build_reverse_maps(self) -> None:
        self._slack_to_normalized.clear()
        self._gchat_to_normalized.clear()
        for normalized, formats in self._emoji_map.items():
            for slack in _as_list(formats["slack"]):
                self._slack_to_normalized[slack.lower()] = normalized
            for gchat in _as_list(formats["gchat"]):
                self._gchat_to_normalized[gchat] = normalized

    def from_slack(self, slack_emoji: str) -> EmojiValueImpl:
        """Convert a Slack emoji name to a normalized :class:`EmojiValue`."""
        cleaned = _COLON_STRIP_RE.sub("", slack_emoji).lower()
        normalized = self._slack_to_normalized.get(cleaned, slack_emoji)
        return get_emoji(normalized)

    def from_gchat(self, gchat_emoji: str) -> EmojiValueImpl:
        """Convert a Google Chat unicode emoji to a normalized :class:`EmojiValue`."""
        normalized = self._gchat_to_normalized.get(gchat_emoji, gchat_emoji)
        return get_emoji(normalized)

    def from_teams(self, teams_reaction: str) -> EmojiValueImpl:
        """Convert a Teams reaction type to a normalized :class:`EmojiValue`."""
        teams_map: dict[str, str] = {
            "like": "thumbs_up",
            "heart": "heart",
            "laugh": "laugh",
            "surprised": "surprised",
            "sad": "sad",
            "angry": "angry",
        }
        normalized = teams_map.get(teams_reaction, teams_reaction)
        return get_emoji(normalized)

    def to_slack(self, emoji: EmojiValueImpl | str) -> str:
        """Convert a normalized emoji (or :class:`EmojiValue`) to Slack format."""
        name = emoji if isinstance(emoji, str) else emoji.name
        formats = self._emoji_map.get(name)
        if formats is None:
            return name
        slack = formats["slack"]
        return slack[0] if isinstance(slack, list) else slack

    def to_gchat(self, emoji: EmojiValueImpl | str) -> str:
        """Convert a normalized emoji (or :class:`EmojiValue`) to Google Chat format."""
        name = emoji if isinstance(emoji, str) else emoji.name
        formats = self._emoji_map.get(name)
        if formats is None:
            return name
        gchat = formats["gchat"]
        return gchat[0] if isinstance(gchat, list) else gchat

    def to_discord(self, emoji: EmojiValueImpl | str) -> str:
        """Convert a normalized emoji (or :class:`EmojiValue`) to Discord (unicode) format."""
        return self.to_gchat(emoji)

    def matches(self, raw_emoji: str, normalized: EmojiValueImpl | str) -> bool:
        """Check whether ``raw_emoji`` (in any platform format) matches ``normalized``."""
        name = normalized if isinstance(normalized, str) else normalized.name
        formats = self._emoji_map.get(name)
        if formats is None:
            return raw_emoji == name

        slack_formats = _as_list(formats["slack"])
        gchat_formats = _as_list(formats["gchat"])

        cleaned_raw = _COLON_STRIP_RE.sub("", raw_emoji).lower()

        return any(s.lower() == cleaned_raw for s in slack_formats) or (raw_emoji in gchat_formats)

    def extend(self, custom_map: EmojiMapConfig) -> None:
        """Add or override emoji mappings in-place."""
        self._emoji_map.update(custom_map)
        self._build_reverse_maps()


default_emoji_resolver: EmojiResolver = EmojiResolver()
"""Default, pre-configured :class:`EmojiResolver` — shared across the package."""


def convert_emoji_placeholders(
    text: str,
    platform: Platform,
    resolver: EmojiResolver | None = None,
) -> str:
    """Expand ``{{emoji:<name>}}`` placeholders in ``text`` for ``platform``.

    ``resolver`` defaults to :data:`default_emoji_resolver` so callers can
    rely on custom emoji registered via :func:`create_emoji` to resolve.
    """
    active = resolver if resolver is not None else default_emoji_resolver

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if platform == "slack":
            return f":{active.to_slack(name)}:"
        # All unicode-based platforms (gchat / teams / discord / github / linear /
        # whatsapp) go through ``to_gchat`` in upstream.
        return active.to_gchat(name)

    return EMOJI_PLACEHOLDER_REGEX.sub(_sub, text)


# ---------------------------------------------------------------------------
# emoji / createEmoji helpers
# ---------------------------------------------------------------------------


_WELL_KNOWN_EMOJI_NAMES: Final[tuple[str, ...]] = (
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
)


class EmojiHelper:
    """Attribute-/item-addressable namespace of :class:`EmojiValue` singletons.

    Upstream returns a plain JS object where both well-known keys (e.g.
    ``emoji.thumbs_up``) and string-indexed access (``emoji["100"]``) work.
    Python keywords don't clash with these names, so attribute access is the
    primary interface, with ``__getitem__`` for keys that aren't valid
    identifiers (``"100"``).
    """

    def __init__(self) -> None:
        # Populate instance ``__dict__`` so Pylance / static tooling can see
        # the attribute access pattern.
        for name in _WELL_KNOWN_EMOJI_NAMES:
            object.__setattr__(self, name, get_emoji(name))

    # ``custom`` is a method on upstream; keep the same shape.
    def custom(self, name: str) -> EmojiValueImpl:
        return get_emoji(name)

    def __getitem__(self, name: str) -> EmojiValueImpl:
        return get_emoji(name)

    def __getattr__(self, name: str) -> EmojiValueImpl:
        # Called only when normal attribute lookup fails — i.e. for custom
        # emoji added via ``create_emoji`` or arbitrary names. We intentionally
        # return a singleton rather than raising so callers can spell any name.
        if name.startswith("_"):
            raise AttributeError(name)
        return get_emoji(name)


def create_emoji(custom_emoji: EmojiMapConfig | None = None) -> EmojiHelper:
    """Create a type-safe emoji helper with optional ``custom_emoji`` registration.

    Custom emoji are registered with :data:`default_emoji_resolver` so
    placeholders expand correctly in messages.
    """
    helper = EmojiHelper()
    if custom_emoji:
        for key in custom_emoji:
            object.__setattr__(helper, key, get_emoji(key))
        default_emoji_resolver.extend(custom_emoji)
    return helper


emoji: EmojiHelper = create_emoji()
"""Default emoji helper — ``emoji.thumbs_up``, ``emoji["100"]``, ``emoji.custom("x")``."""
