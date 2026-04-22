"""Telegram cards translator.

Python port of upstream ``packages/adapter-telegram/src/cards.ts``. Converts
card ``ActionsElement`` children to Telegram inline keyboard markup and
encodes/decodes the callback-data payload used by action buttons.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from chat import convert_emoji_placeholders
from chat_adapter_shared import ValidationError

from .types import TelegramInlineKeyboardButton, TelegramInlineKeyboardMarkup

_CALLBACK_DATA_PREFIX = "chat:"
_TELEGRAM_CALLBACK_DATA_LIMIT_BYTES = 64


class _TelegramCardActionPayload(TypedDict, total=False):
    a: str
    v: str


def _convert_label(label: str) -> str:
    return convert_emoji_placeholders(label, "gchat")


def _to_inline_keyboard_row(actions: dict[str, Any]) -> list[TelegramInlineKeyboardButton]:
    row: list[TelegramInlineKeyboardButton] = []
    for action in actions.get("children") or []:
        atype = action.get("type")
        if atype == "button":
            row.append(
                {
                    "text": _convert_label(str(action.get("label", ""))),
                    "callback_data": encode_telegram_callback_data(
                        str(action.get("id", "")),
                        action.get("value") if isinstance(action.get("value"), str) else None,
                    ),
                },
            )
            continue
        if atype == "link-button":
            row.append(
                {
                    "text": _convert_label(str(action.get("label", ""))),
                    "url": str(action.get("url", "")),
                },
            )
    return row


def _collect_inline_keyboard_rows(
    children: list[dict[str, Any]],
    rows: list[list[TelegramInlineKeyboardButton]],
) -> None:
    for child in children:
        ctype = child.get("type")
        if ctype == "actions":
            row = _to_inline_keyboard_row(child)
            if row:
                rows.append(row)
            continue
        if ctype == "section":
            _collect_inline_keyboard_rows(child.get("children") or [], rows)


def card_to_telegram_inline_keyboard(
    card: dict[str, Any],
) -> TelegramInlineKeyboardMarkup | None:
    """Render a :class:`chat.CardElement` dict to Telegram inline keyboard markup.

    Returns ``None`` when the card has no action buttons — Telegram does not
    accept an empty ``inline_keyboard``.
    """

    rows: list[list[TelegramInlineKeyboardButton]] = []
    _collect_inline_keyboard_rows(card.get("children") or [], rows)
    if not rows:
        return None
    return {"inline_keyboard": rows}


def empty_telegram_inline_keyboard() -> TelegramInlineKeyboardMarkup:
    """Return an empty inline keyboard — used to strip buttons on ``editMessageText``."""

    return {"inline_keyboard": []}


def encode_telegram_callback_data(action_id: str, value: str | None = None) -> str:
    """Encode an ``actionId`` / ``value`` pair into a Telegram callback payload.

    Raises :class:`ValidationError` when the encoded payload exceeds the
    Telegram 64-byte callback limit.
    """

    payload: _TelegramCardActionPayload = {"a": action_id}
    if isinstance(value, str):
        payload["v"] = value

    callback_data = f"{_CALLBACK_DATA_PREFIX}{json.dumps(payload, separators=(',', ':'))}"
    if len(callback_data.encode("utf-8")) > _TELEGRAM_CALLBACK_DATA_LIMIT_BYTES:
        raise ValidationError(
            "telegram",
            f"Callback payload too large for Telegram (max {_TELEGRAM_CALLBACK_DATA_LIMIT_BYTES} bytes).",
        )
    return callback_data


def decode_telegram_callback_data(
    data: str | None,
) -> dict[str, str | None]:
    """Decode a Telegram callback payload into ``{actionId, value}``.

    Falls back to passthrough behavior for legacy / unparseable payloads.
    """

    if not data:
        return {"actionId": "telegram_callback", "value": None}

    if not data.startswith(_CALLBACK_DATA_PREFIX):
        return {"actionId": data, "value": data}

    try:
        decoded = json.loads(data[len(_CALLBACK_DATA_PREFIX) :])
    except (ValueError, json.JSONDecodeError):
        return {"actionId": data, "value": data}

    if isinstance(decoded, dict):
        action_id = decoded.get("a")
        if isinstance(action_id, str) and action_id:
            raw_value = decoded.get("v")
            value = raw_value if isinstance(raw_value, str) else None
            return {"actionId": action_id, "value": value}

    return {"actionId": data, "value": data}


__all__ = [
    "card_to_telegram_inline_keyboard",
    "decode_telegram_callback_data",
    "empty_telegram_inline_keyboard",
    "encode_telegram_callback_data",
]
