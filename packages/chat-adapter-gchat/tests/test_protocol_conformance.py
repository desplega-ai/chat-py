"""GChat adapter structural conformance against ``chat.types.Adapter``.

This test intentionally stays RED until Phase 3 of DES-196 lands the GChat
Part-B dispatch surface (``handle_webhook`` / ``post_message`` / etc.).
Keeping it red is the phase's "done" signal.
"""

from __future__ import annotations

from chat.types import Adapter
from chat_adapter_gchat import create_google_chat_adapter


def test_gchat_adapter_implements_adapter_protocol(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
    adapter = create_google_chat_adapter()
    assert isinstance(adapter, Adapter), "GoogleChatAdapter missing Protocol methods"
