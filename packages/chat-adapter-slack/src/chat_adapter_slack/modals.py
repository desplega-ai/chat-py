"""Slack modal (view) converter.

Python port of upstream ``packages/adapter-slack/src/modals.ts``.

Converts ``ModalElement`` dicts to Slack Block Kit ``view`` payloads. Like
:mod:`.cards`, modal elements use dict-first / attribute-fallback semantics.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypedDict

from .cards import (
    SlackBlock,
    convert_fields_to_block,
    convert_text_to_block,
)

if TYPE_CHECKING:
    from chat import (
        ExternalSelectElement,
        ModalChild,
        ModalElement,
        RadioSelectElement,
        SelectElement,
        SelectOptionElement,
        TextInputElement,
    )

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class _PlainText(TypedDict):
    type: str
    text: str


class SlackView(TypedDict, total=False):
    """A Slack ``view`` object — the payload for ``views.open``/``views.update``."""

    blocks: list[SlackBlock]
    callback_id: str
    close: _PlainText
    notify_on_close: bool
    private_metadata: str
    submit: _PlainText
    title: _PlainText
    type: str


class SlackModalResponse(TypedDict, total=False):
    """Response payload for modal interactions."""

    errors: dict[str, str]
    response_action: str
    view: SlackView


class SlackOptionObject(TypedDict, total=False):
    """An option inside a ``static_select``/``radio_buttons`` element."""

    description: _PlainText
    text: _PlainText
    value: str


# ---------------------------------------------------------------------------
# Dict-first / attribute-fallback reader (mirrors the helper in cards.py)
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        value = obj.get(key, default)
        return value if value is not None else default
    value = getattr(obj, key, default)
    return value if value is not None else default


def _prop(obj: Any, *keys: str) -> Any:
    """Read the first present prop across camelCase / snake_case aliases."""

    for key in keys:
        if isinstance(obj, dict) and key in obj and obj[key] is not None:
            return obj[key]
        if not isinstance(obj, dict):
            attr = getattr(obj, key, None)
            if attr is not None:
                return attr
    return None


# ---------------------------------------------------------------------------
# Private metadata encoding
# ---------------------------------------------------------------------------


class ModalMetadata(TypedDict, total=False):
    """Packed metadata for Slack's ``private_metadata`` field."""

    contextId: str
    privateMetadata: str


def encode_modal_metadata(meta: ModalMetadata) -> str | None:
    """Serialize :class:`ModalMetadata` into Slack's ``private_metadata`` string.

    Returns ``None`` when both fields are empty so callers can omit the field.
    """

    context_id = meta.get("contextId")
    private_metadata = meta.get("privateMetadata")
    if not (context_id or private_metadata):
        return None
    payload: dict[str, Any] = {"c": context_id, "m": private_metadata}
    return json.dumps(payload)


def decode_modal_metadata(raw: str | None) -> ModalMetadata:
    """Parse Slack's ``private_metadata`` back into :class:`ModalMetadata`.

    Falls back to treating the raw string as a plain ``contextId`` for
    backward-compatibility with older encodings.
    """

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {"contextId": raw}
    if isinstance(parsed, dict) and ("c" in parsed or "m" in parsed):
        c = parsed.get("c") or None
        m = parsed.get("m") or None
        # Match upstream's shape: always include both keys (None when absent).
        return {
            "contextId": c,  # type: ignore[typeddict-item]
            "privateMetadata": m,  # type: ignore[typeddict-item]
        }
    return {"contextId": raw}


# ---------------------------------------------------------------------------
# Modal view conversion
# ---------------------------------------------------------------------------


def modal_to_slack_view(
    modal: ModalElement | dict[str, Any],
    context_id: str | None = None,
) -> SlackView:
    """Convert a ``ModalElement`` dict to a Slack ``view`` payload."""

    title = str(_prop(modal, "title") or "")
    submit_label = _prop(modal, "submitLabel", "submit_label")
    close_label = _prop(modal, "closeLabel", "close_label")
    notify_on_close = _prop(modal, "notifyOnClose", "notify_on_close")
    callback_id = _prop(modal, "callbackId", "callback_id") or ""
    children = _get(modal, "children", []) or []

    view: SlackView = {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title[:24]},
        "submit": {
            "type": "plain_text",
            "text": submit_label if submit_label else "Submit",
        },
        "close": {
            "type": "plain_text",
            "text": close_label if close_label else "Cancel",
        },
        "blocks": [_modal_child_to_block(child) for child in children],
    }

    if notify_on_close is not None:
        view["notify_on_close"] = bool(notify_on_close)
    if context_id is not None:
        view["private_metadata"] = context_id

    return view


def _modal_child_to_block(child: ModalChild | dict[str, Any]) -> SlackBlock:
    child_type = _get(child, "type")
    if child_type == "text_input":
        return _text_input_to_block(child)
    if child_type == "select":
        return _select_to_block(child)
    if child_type == "external_select":
        return _external_select_to_block(child)
    if child_type == "radio_select":
        return _radio_select_to_block(child)
    if child_type == "text":
        return convert_text_to_block(child)
    if child_type == "fields":
        return convert_fields_to_block(child)
    raise ValueError(f"Unknown modal child type: {child_type}")


# ---------------------------------------------------------------------------
# Option conversion
# ---------------------------------------------------------------------------


def select_option_to_slack_option(
    option: SelectOptionElement | dict[str, Any],
) -> SlackOptionObject:
    """Convert a ``SelectOptionElement`` to a Slack option dict (plain_text)."""

    out: SlackOptionObject = {
        "text": {"type": "plain_text", "text": _get(option, "label", "")},
        "value": _get(option, "value", ""),
    }
    description = _get(option, "description")
    if description:
        out["description"] = {"type": "plain_text", "text": description}
    return out


# ---------------------------------------------------------------------------
# Input element converters
# ---------------------------------------------------------------------------


def _text_input_to_block(input_: TextInputElement | dict[str, Any]) -> SlackBlock:
    element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": _get(input_, "id", ""),
        "multiline": bool(_get(input_, "multiline", False)),
    }
    placeholder = _get(input_, "placeholder")
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}
    initial_value = _prop(input_, "initialValue", "initial_value")
    if initial_value:
        element["initial_value"] = initial_value
    max_length = _prop(input_, "maxLength", "max_length")
    if max_length:
        element["max_length"] = max_length

    return {
        "type": "input",
        "block_id": _get(input_, "id", ""),
        "optional": bool(_get(input_, "optional", False)),
        "label": {"type": "plain_text", "text": _get(input_, "label", "")},
        "element": element,
    }


def _select_to_block(select: SelectElement | dict[str, Any]) -> SlackBlock:
    options = [select_option_to_slack_option(opt) for opt in _get(select, "options", []) or []]

    element: dict[str, Any] = {
        "type": "static_select",
        "action_id": _get(select, "id", ""),
        "options": options,
    }

    placeholder = _get(select, "placeholder")
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}

    initial_option = _prop(select, "initialOption", "initial_option")
    if initial_option:
        for opt in options:
            if opt.get("value") == initial_option:
                element["initial_option"] = opt
                break

    return {
        "type": "input",
        "block_id": _get(select, "id", ""),
        "optional": bool(_get(select, "optional", False)),
        "label": {"type": "plain_text", "text": _get(select, "label", "")},
        "element": element,
    }


def _external_select_to_block(
    select: ExternalSelectElement | dict[str, Any],
) -> SlackBlock:
    element: dict[str, Any] = {
        "type": "external_select",
        "action_id": _get(select, "id", ""),
    }

    placeholder = _get(select, "placeholder")
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}

    min_query_length = _prop(select, "minQueryLength", "min_query_length")
    if min_query_length is not None:
        element["min_query_length"] = min_query_length

    return {
        "type": "input",
        "block_id": _get(select, "id", ""),
        "optional": bool(_get(select, "optional", False)),
        "label": {"type": "plain_text", "text": _get(select, "label", "")},
        "element": element,
    }


def _radio_select_to_block(
    radio: RadioSelectElement | dict[str, Any],
) -> SlackBlock:
    limited = (_get(radio, "options", []) or [])[:10]
    options: list[dict[str, Any]] = []
    for opt in limited:
        option: dict[str, Any] = {
            "text": {"type": "mrkdwn", "text": _get(opt, "label", "")},
            "value": _get(opt, "value", ""),
        }
        description = _get(opt, "description")
        if description:
            option["description"] = {"type": "mrkdwn", "text": description}
        options.append(option)

    element: dict[str, Any] = {
        "type": "radio_buttons",
        "action_id": _get(radio, "id", ""),
        "options": options,
    }

    initial_option = _prop(radio, "initialOption", "initial_option")
    if initial_option:
        for opt in options:
            if opt.get("value") == initial_option:
                element["initial_option"] = opt
                break

    return {
        "type": "input",
        "block_id": _get(radio, "id", ""),
        "optional": bool(_get(radio, "optional", False)),
        "label": {"type": "plain_text", "text": _get(radio, "label", "")},
        "element": element,
    }


__all__ = [
    "ModalMetadata",
    "SlackModalResponse",
    "SlackOptionObject",
    "SlackView",
    "decode_modal_metadata",
    "encode_modal_metadata",
    "modal_to_slack_view",
    "select_option_to_slack_option",
]
