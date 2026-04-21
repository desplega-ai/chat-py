"""Card/Modal element helpers — Python analogue of ``jsx-runtime.ts``.

There is no JSX in Python, so this module exposes:

1. A centralized re-export of every card/modal builder function from
   :mod:`chat.cards` and :mod:`chat.modals`. Callers can write
   ``from chat.jsx_runtime import Card, Text, Button, Modal`` to get the
   same names that upstream TS uses under ``/** @jsxImportSource chat */``.
2. :func:`is_jsx` / :func:`to_card_element` / :func:`to_modal_element` —
   lightweight type guards and coercions matching the upstream exports.
3. :func:`card` — a decorator that marks a function as a "card factory",
   caching the fact that its return value is a :class:`CardElement`. This
   matches the Python-idiomatic sibling of `@jsxImportSource chat`.

Upstream's ``jsx`` / ``jsxs`` / ``Fragment`` factories are deliberately
omitted — they exist only to satisfy the TS ``react-jsx`` transform. The
Python port calls builder functions directly (``Card(children=[...])``)
per the translation table in the chat-py ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

from chat.cards import (
    Actions,
    ActionsElement,
    Button,
    ButtonElement,
    Card,
    CardElement,
    CardLink,
    Divider,
    DividerElement,
    Field,
    FieldElement,
    Fields,
    FieldsElement,
    Image,
    ImageElement,
    LinkButton,
    LinkButtonElement,
    LinkElement,
    Section,
    SectionElement,
    Table,
    TableElement,
    Text,
    TextElement,
    is_card_element,
)
from chat.modals import (
    Modal,
    ModalElement,
    RadioSelect,
    RadioSelectElement,
    Select,
    SelectElement,
    SelectOption,
    SelectOptionElement,
    TextInput,
    TextInputElement,
    filter_modal_children,
    is_modal_element,
)


def is_jsx(value: Any) -> bool:
    """Return ``True`` if *value* is a card, modal, or modal-child element.

    Mirrors upstream's :func:`isJSX` check — any dict with a ``type`` key
    recognized by :func:`is_card_element` or :func:`is_modal_element` or
    with one of the known child ``type`` strings.
    """
    if not isinstance(value, dict) or "type" not in value:
        return False
    if is_card_element(value) or is_modal_element(value):
        return True
    return value["type"] in _KNOWN_ELEMENT_TYPES


def to_card_element(value: Any) -> CardElement | None:
    """Return *value* as a :class:`CardElement` if it already is one."""
    if is_card_element(value):
        return value  # type: ignore[no-any-return]
    return None


def to_modal_element(value: Any) -> ModalElement | None:
    """Return *value* as a :class:`ModalElement` if it already is one."""
    if is_modal_element(value):
        return value  # type: ignore[no-any-return]
    return None


def card[F](fn: F) -> F:
    """Decorator: mark *fn* as a card-builder.

    Purely advisory — sets ``fn.__chat_card__ = True`` so callers can
    detect card factories without invoking them. Analogous to the upstream
    JSX component registration (``CardComponent``/``TextComponent``).
    """
    fn.__chat_card__ = True  # type: ignore[attr-defined]
    return fn


_KNOWN_ELEMENT_TYPES: frozenset[str] = frozenset(
    {
        "card",
        "text",
        "button",
        "link_button",
        "link",
        "image",
        "divider",
        "section",
        "actions",
        "fields",
        "field",
        "table",
        "modal",
        "text_input",
        "select",
        "select_option",
        "radio_select",
    }
)


__all__ = [
    "Actions",
    "ActionsElement",
    "Button",
    "ButtonElement",
    "Card",
    "CardElement",
    "CardLink",
    "Divider",
    "DividerElement",
    "Field",
    "FieldElement",
    "Fields",
    "FieldsElement",
    "Image",
    "ImageElement",
    "LinkButton",
    "LinkButtonElement",
    "LinkElement",
    "Modal",
    "ModalElement",
    "RadioSelect",
    "RadioSelectElement",
    "Section",
    "SectionElement",
    "Select",
    "SelectElement",
    "SelectOption",
    "SelectOptionElement",
    "Table",
    "TableElement",
    "Text",
    "TextElement",
    "TextInput",
    "TextInputElement",
    "card",
    "filter_modal_children",
    "is_card_element",
    "is_jsx",
    "is_modal_element",
    "to_card_element",
    "to_modal_element",
]
