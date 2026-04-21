"""Dict-based card/modal builders for tests.

Matches upstream TS builder ergonomics (``Card()``, ``Button()``, etc.) while
using the dict-based AST convention adopted by chat-py. Private to the tests
in this package — the public card API lives in ``chat.cards`` (ported by the
core team in parallel).
"""

from __future__ import annotations

from typing import Any


def Card(
    *,
    title: str | None = None,
    subtitle: str | None = None,
    image_url: str | None = None,
    imageUrl: str | None = None,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card: dict[str, Any] = {"children": children or []}
    if title is not None:
        card["title"] = title
    if subtitle is not None:
        card["subtitle"] = subtitle
    if imageUrl is not None:
        card["imageUrl"] = imageUrl
    if image_url is not None:
        card["imageUrl"] = image_url
    return card


def CardText(content: str, *, style: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "content": content}
    if style is not None:
        node["style"] = style
    return node


def CardLink(*, url: str, label: str) -> dict[str, Any]:
    return {"type": "link", "url": url, "label": label}


def Image(*, url: str, alt: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "image", "url": url}
    if alt is not None:
        node["alt"] = alt
    return node


def Divider() -> dict[str, Any]:
    return {"type": "divider"}


def Actions(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "actions", "children": children}


def Section(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "section", "children": children}


def Fields(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "fields", "children": children}


def Field(*, label: str, value: str) -> dict[str, Any]:
    return {"type": "field", "label": label, "value": value}


def Button(
    *,
    id: str,
    label: str,
    style: str | None = None,
    value: str | None = None,
    action_type: str | None = None,
    disabled: bool | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "button", "id": id, "label": label}
    if style is not None:
        node["style"] = style
    if value is not None:
        node["value"] = value
    if action_type is not None:
        node["actionType"] = action_type
    if disabled is not None:
        node["disabled"] = disabled
    return node


def LinkButton(*, url: str, label: str, style: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "link-button", "url": url, "label": label}
    if style is not None:
        node["style"] = style
    return node


def Select(
    *,
    id: str,
    label: str,
    options: list[dict[str, Any]],
    placeholder: str | None = None,
    initial_option: str | None = None,
    initialOption: str | None = None,
    optional: bool | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "select",
        "id": id,
        "label": label,
        "options": options,
    }
    if placeholder is not None:
        node["placeholder"] = placeholder
    chosen_initial = initial_option if initial_option is not None else initialOption
    if chosen_initial is not None:
        node["initialOption"] = chosen_initial
    if optional is not None:
        node["optional"] = optional
    return node


def RadioSelect(
    *,
    id: str,
    label: str,
    options: list[dict[str, Any]],
    initial_option: str | None = None,
    initialOption: str | None = None,
    optional: bool | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "radio_select",
        "id": id,
        "label": label,
        "options": options,
    }
    chosen_initial = initial_option if initial_option is not None else initialOption
    if chosen_initial is not None:
        node["initialOption"] = chosen_initial
    if optional is not None:
        node["optional"] = optional
    return node


def SelectOption(
    *,
    label: str,
    value: str,
    description: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"label": label, "value": value}
    if description is not None:
        node["description"] = description
    return node


def ExternalSelect(
    *,
    id: str,
    label: str,
    placeholder: str | None = None,
    min_query_length: int | None = None,
    minQueryLength: int | None = None,
    optional: bool | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "external_select", "id": id, "label": label}
    if placeholder is not None:
        node["placeholder"] = placeholder
    chosen_min = min_query_length if min_query_length is not None else minQueryLength
    if chosen_min is not None:
        node["minQueryLength"] = chosen_min
    if optional is not None:
        node["optional"] = optional
    return node


def TextInput(
    *,
    id: str,
    label: str,
    multiline: bool | None = None,
    placeholder: str | None = None,
    initial_value: str | None = None,
    initialValue: str | None = None,
    max_length: int | None = None,
    maxLength: int | None = None,
    optional: bool | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text_input", "id": id, "label": label}
    if multiline is not None:
        node["multiline"] = multiline
    if placeholder is not None:
        node["placeholder"] = placeholder
    chosen_initial = initial_value if initial_value is not None else initialValue
    if chosen_initial is not None:
        node["initialValue"] = chosen_initial
    chosen_max = max_length if max_length is not None else maxLength
    if chosen_max is not None:
        node["maxLength"] = chosen_max
    if optional is not None:
        node["optional"] = optional
    return node


def Table(
    *,
    headers: list[str],
    rows: list[list[str]],
    align: list[str] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "table", "headers": headers, "rows": rows}
    if align is not None:
        node["align"] = align
    return node


def Modal(
    *,
    callback_id: str | None = None,
    callbackId: str | None = None,
    title: str,
    children: list[dict[str, Any]] | None = None,
    submit_label: str | None = None,
    submitLabel: str | None = None,
    close_label: str | None = None,
    closeLabel: str | None = None,
    notify_on_close: bool | None = None,
    notifyOnClose: bool | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "modal",
        "title": title,
        "children": children or [],
    }
    chosen_cb = callback_id if callback_id is not None else callbackId
    if chosen_cb is not None:
        node["callbackId"] = chosen_cb
    chosen_submit = submit_label if submit_label is not None else submitLabel
    if chosen_submit is not None:
        node["submitLabel"] = chosen_submit
    chosen_close = close_label if close_label is not None else closeLabel
    if chosen_close is not None:
        node["closeLabel"] = chosen_close
    chosen_notify = notify_on_close if notify_on_close is not None else notifyOnClose
    if chosen_notify is not None:
        node["notifyOnClose"] = chosen_notify
    return node
