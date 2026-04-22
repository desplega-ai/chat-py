"""Tests that ``chat.types.Adapter`` is a runtime-checkable Protocol.

The Slack/GChat dispatch gap (DES-196) reached a release candidate because
``Adapter`` was aliased to ``Any`` — no structural check caught missing
``handle_webhook`` / ``post_message`` / ``edit_message`` methods. These tests
pin the Protocol in place so downstream ``isinstance(adapter, Adapter)``
conformance checks can actually do their job.
"""

from __future__ import annotations

from chat.types import Adapter


def test_adapter_is_runtime_protocol() -> None:
    assert getattr(Adapter, "_is_protocol", False) is True, (
        "Adapter must be a Protocol (currently aliased to Any)"
    )


def test_adapter_declares_core_dispatch_surface() -> None:
    required = {
        "name",
        "initialize",
        "handle_webhook",
        "encode_thread_id",
        "decode_thread_id",
        "channel_id_from_thread_id",
        "post_message",
        "edit_message",
        "delete_message",
        "add_reaction",
        "remove_reaction",
    }
    present = {m for m in dir(Adapter) if not m.startswith("_")}
    missing = required - present
    assert not missing, f"Adapter Protocol missing: {missing}"
