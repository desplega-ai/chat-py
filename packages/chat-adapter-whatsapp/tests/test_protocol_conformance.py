"""WhatsApp adapter structural conformance against ``chat.types.Adapter``.

Mirrors the Slack / GChat / Discord / GitHub conformance tests from earlier
DES-196 phases. Pins that :class:`WhatsAppAdapter` satisfies the structural
``Adapter`` Protocol so it can be used interchangeably by :class:`chat.Chat`.
"""

from __future__ import annotations

from chat.types import Adapter
from chat_adapter_whatsapp import create_whatsapp_adapter


def test_whatsapp_adapter_implements_adapter_protocol(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    adapter = create_whatsapp_adapter()
    assert isinstance(adapter, Adapter), "WhatsAppAdapter missing Protocol methods"
