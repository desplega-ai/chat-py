"""Pub/Sub push envelope handling for the Google Chat adapter.

Python port of upstream ``packages/adapter-gchat/src/pubsub.ts``.

Google Chat apps can receive events via two transports:

1. **Direct HTTP webhook** — Google POSTs JSON directly to the app endpoint.
2. **Pub/Sub push** — Google publishes to a Pub/Sub topic, Pub/Sub POSTs a
   CloudEvents-shaped envelope to the app endpoint.

This module owns the shape detection + unwrapping for the Pub/Sub path so
:func:`chat_adapter_gchat.GoogleChatAdapter.handle_webhook` can funnel both
paths through the same dispatch logic.
"""

from __future__ import annotations

import base64
import json
from typing import Any


def is_pubsub_envelope(payload: dict[str, Any]) -> bool:
    """Return ``True`` if ``payload`` looks like a Pub/Sub push envelope.

    Pub/Sub envelopes always contain a top-level ``message`` object with at
    least ``data`` and/or ``attributes`` keys — and never the Google Chat
    event fields (``type`` / ``chat`` / ``message.argumentText``). We detect
    on the presence of the ``message.data`` + ``attributes`` shape rather
    than the absence of Chat fields so unknown Chat event shapes don't get
    mis-classified.
    """

    message = payload.get("message")
    if not isinstance(message, dict):
        return False
    # Pub/Sub push envelopes carry either `data` (base64 JSON) or empty.
    return "data" in message or "attributes" in message


def decode_pubsub_envelope(
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Unwrap a Pub/Sub push envelope into ``(event_payload, attributes)``.

    The ``event_payload`` is the JSON-decoded Chat event (what a direct HTTP
    webhook would have delivered); ``attributes`` carries the CloudEvents
    metadata (``ce-type``, ``ce-subject``, ``ce-time``) the dispatcher needs
    to pick the right branch.

    Returns ``({}, attributes)`` when the data field is empty or malformed —
    mirrors upstream behavior of ack'ing empty envelopes as 2xx no-ops.
    """

    message = envelope.get("message") or {}
    attributes_raw = message.get("attributes") or {}
    attributes: dict[str, str] = {
        str(k): str(v) for k, v in attributes_raw.items() if v is not None
    }

    encoded = message.get("data")
    if not encoded:
        return {}, attributes

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, TypeError):
        return {}, attributes
    if not decoded:
        return {}, attributes
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        return {}, attributes
    if not isinstance(payload, dict):
        return {}, attributes
    return payload, attributes


__all__ = [
    "decode_pubsub_envelope",
    "is_pubsub_envelope",
]
