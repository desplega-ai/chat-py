"""Slack adapter structural conformance against ``chat.types.Adapter``.

This test intentionally stays RED until Phase 1 of DES-196 lands the Slack
Part-B dispatch surface (``handle_webhook`` / ``post_message`` / etc.).
Keeping it red is the phase's "done" signal.
"""

from __future__ import annotations

from chat.types import Adapter
from chat_adapter_slack import create_slack_adapter


def test_slack_adapter_implements_adapter_protocol(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test")
    adapter = create_slack_adapter()
    assert isinstance(adapter, Adapter), "SlackAdapter missing Protocol methods"
