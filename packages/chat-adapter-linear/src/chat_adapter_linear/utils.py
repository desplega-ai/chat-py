"""Small helpers shared by the Linear adapter modules.

Python port of upstream ``packages/adapter-linear/src/utils.ts``.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from chat_adapter_shared import ValidationError, extract_card

from .cards import card_to_linear_markdown
from .types import LinearThreadId

if TYPE_CHECKING:
    from chat import AdapterPostableMessage

    from .markdown import LinearFormatConverter

_PROFILE_URL_REGEX = re.compile(r"^https://linear\.app/\S+/profiles/([^/?#]+)")


def render_message_to_linear_markdown(
    message: AdapterPostableMessage | str,
    format_converter: LinearFormatConverter,
) -> str:
    """Render any :data:`AdapterPostableMessage` to Linear-flavored markdown.

    Cards bypass :class:`LinearFormatConverter` and go through
    :func:`card_to_linear_markdown` directly; everything else is funneled
    through the converter's :meth:`~LinearFormatConverter.render_postable`.
    Emoji placeholders are converted after rendering.
    """

    from chat import convert_emoji_placeholders

    card = extract_card(message)
    rendered = (
        card_to_linear_markdown(dict(card)) if card else format_converter.render_postable(message)
    )
    return convert_emoji_placeholders(rendered, "linear")


def assert_agent_session_thread(thread: LinearThreadId) -> None:
    """Narrow a :class:`LinearThreadId` to the agent-session variant.

    Raises :class:`ValidationError` if ``agentSessionId`` is missing.
    """

    if not thread.get("agentSessionId"):
        raise ValidationError("linear", "Expected a Linear agent session thread")


def get_user_name_from_profile_url(url: str | None) -> str:
    """Return the user slug from a ``https://linear.app/<ws>/profiles/<slug>`` URL.

    Bit of a hack — mirrors upstream, avoids extra API round-trips just to
    get the display name.
    """

    if not url:
        return ""
    match = _PROFILE_URL_REGEX.match(url)
    return match.group(1) if match else ""


def calculate_expiry(expires_in: int | None) -> int | None:
    """Convert an OAuth ``expires_in`` (seconds) into a future epoch milliseconds value."""

    if expires_in is None:
        return None
    return int(time.time() * 1000) + int(expires_in) * 1000


def installation_from_dict(value: Any) -> dict[str, Any] | None:
    """Coerce a raw state value into an installation dict or ``None``."""

    if isinstance(value, dict):
        return value
    return None


__all__ = [
    "assert_agent_session_thread",
    "calculate_expiry",
    "get_user_name_from_profile_url",
    "installation_from_dict",
    "render_message_to_linear_markdown",
]
