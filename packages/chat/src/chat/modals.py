"""Modal elements for form dialogs — Python port of ``packages/chat/src/modals.ts``.

Modals are dict-based, with camelCase keys for wire compatibility with upstream
TypeScript SDK payloads. Builder names stay PascalCase (``Modal``, ``TextInput``,
``Select``, ``RadioSelect``, ``SelectOption``) to match upstream docs and
JSX-style composition — we do not port the React element conversion helpers
(``fromReactModalElement``); Python composes modals with nested builder calls.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Required, TypedDict

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Element shapes
# ---------------------------------------------------------------------------


VALID_MODAL_CHILD_TYPES: tuple[str, ...] = (
    "text_input",
    "select",
    "radio_select",
    "text",
    "fields",
)
"""Runtime discriminator set for modal children — matches upstream exactly."""


class TextInputElement(TypedDict, total=False):
    """Free-form text input field."""

    type: Required[Literal["text_input"]]
    id: Required[str]
    label: Required[str]
    placeholder: str | None
    initialValue: str | None
    multiline: bool | None
    optional: bool | None
    maxLength: int | None


class SelectOptionElement(TypedDict, total=False):
    """Option inside a :class:`SelectElement` or :class:`RadioSelectElement`."""

    label: Required[str]
    value: Required[str]
    description: str | None


class SelectElement(TypedDict, total=False):
    """Dropdown select input."""

    type: Required[Literal["select"]]
    id: Required[str]
    label: Required[str]
    options: Required[list[SelectOptionElement]]
    placeholder: str | None
    initialOption: str | None
    optional: bool | None


class RadioSelectElement(TypedDict, total=False):
    """Radio-style single-choice select."""

    type: Required[Literal["radio_select"]]
    id: Required[str]
    label: Required[str]
    options: Required[list[SelectOptionElement]]
    initialOption: str | None
    optional: bool | None


# ``ModalChild`` is validated at runtime against ``VALID_MODAL_CHILD_TYPES``.
# We keep the declared child shape loose (``dict``) to avoid a circular import
# with :mod:`chat.cards` for ``TextElement`` / ``FieldsElement``.
ModalChild = dict[str, Any]


class ModalElement(TypedDict, total=False):
    """Root modal dialog element."""

    type: Required[Literal["modal"]]
    callbackId: Required[str]
    title: Required[str]
    children: Required[list[ModalChild]]
    submitLabel: str | None
    closeLabel: str | None
    notifyOnClose: bool | None
    privateMetadata: str | None


# ---------------------------------------------------------------------------
# Type guards
# ---------------------------------------------------------------------------


def is_modal_element(value: Any) -> bool:
    """Return ``True`` if *value* is a :class:`ModalElement` dict."""
    return isinstance(value, dict) and value.get("type") == "modal"


def filter_modal_children(children: list[Any]) -> list[ModalChild]:
    """Return the subset of *children* with a valid modal ``type``.

    Emits a warning via :mod:`logging` if any children were dropped — matches
    upstream ``console.warn`` behaviour.
    """
    valid: list[ModalChild] = []
    for child in children:
        if (
            isinstance(child, dict)
            and isinstance(child.get("type"), str)
            and child["type"] in VALID_MODAL_CHILD_TYPES
        ):
            valid.append(child)
    if len(valid) < len(children):
        _log.warning("[chat] Modal contains unsupported child elements that were ignored")
    return valid


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------


def Modal(
    *,
    callback_id: str,
    title: str,
    children: list[ModalChild] | None = None,
    submit_label: str | None = None,
    close_label: str | None = None,
    notify_on_close: bool | None = None,
    private_metadata: str | None = None,
) -> ModalElement:
    """Create a :class:`ModalElement`."""
    modal: ModalElement = {
        "type": "modal",
        "callbackId": callback_id,
        "title": title,
        "children": list(children or []),
    }
    if submit_label is not None:
        modal["submitLabel"] = submit_label
    if close_label is not None:
        modal["closeLabel"] = close_label
    if notify_on_close is not None:
        modal["notifyOnClose"] = notify_on_close
    if private_metadata is not None:
        modal["privateMetadata"] = private_metadata
    return modal


def TextInput(
    *,
    id: str,
    label: str,
    placeholder: str | None = None,
    initial_value: str | None = None,
    multiline: bool | None = None,
    optional: bool | None = None,
    max_length: int | None = None,
) -> TextInputElement:
    """Create a :class:`TextInputElement`."""
    node: TextInputElement = {"type": "text_input", "id": id, "label": label}
    if placeholder is not None:
        node["placeholder"] = placeholder
    if initial_value is not None:
        node["initialValue"] = initial_value
    if multiline is not None:
        node["multiline"] = multiline
    if optional is not None:
        node["optional"] = optional
    if max_length is not None:
        node["maxLength"] = max_length
    return node


def Select(
    *,
    id: str,
    label: str,
    options: list[SelectOptionElement],
    placeholder: str | None = None,
    initial_option: str | None = None,
    optional: bool | None = None,
) -> SelectElement:
    """Create a :class:`SelectElement`. Raises ``ValueError`` if *options* is empty."""
    if not options:
        raise ValueError("Select requires at least one option")
    node: SelectElement = {
        "type": "select",
        "id": id,
        "label": label,
        "options": list(options),
    }
    if placeholder is not None:
        node["placeholder"] = placeholder
    if initial_option is not None:
        node["initialOption"] = initial_option
    if optional is not None:
        node["optional"] = optional
    return node


def SelectOption(
    *,
    label: str,
    value: str,
    description: str | None = None,
) -> SelectOptionElement:
    """Create a :class:`SelectOptionElement`."""
    opt: SelectOptionElement = {"label": label, "value": value}
    if description is not None:
        opt["description"] = description
    return opt


def RadioSelect(
    *,
    id: str,
    label: str,
    options: list[SelectOptionElement],
    initial_option: str | None = None,
    optional: bool | None = None,
) -> RadioSelectElement:
    """Create a :class:`RadioSelectElement`. Raises ``ValueError`` if *options* is empty."""
    if not options:
        raise ValueError("RadioSelect requires at least one option")
    node: RadioSelectElement = {
        "type": "radio_select",
        "id": id,
        "label": label,
        "options": list(options),
    }
    if initial_option is not None:
        node["initialOption"] = initial_option
    if optional is not None:
        node["optional"] = optional
    return node


__all__ = [
    "VALID_MODAL_CHILD_TYPES",
    "Modal",
    "ModalChild",
    "ModalElement",
    "RadioSelect",
    "RadioSelectElement",
    "Select",
    "SelectElement",
    "SelectOption",
    "SelectOptionElement",
    "TextInput",
    "TextInputElement",
    "filter_modal_children",
    "is_modal_element",
]
