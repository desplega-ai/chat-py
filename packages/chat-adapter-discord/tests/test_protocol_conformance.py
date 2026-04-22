"""Discord adapter structural conformance against ``chat.types.Adapter``.

Mirrors the Slack / GChat conformance tests from Phase 1 / Phase 3 of DES-196.
Pins that ``DiscordAdapter`` satisfies the structural ``Adapter`` Protocol, so
it can be used interchangeably by :class:`chat.Chat`.
"""

from __future__ import annotations

from chat.types import Adapter
from chat_adapter_discord import create_discord_adapter


def test_discord_adapter_implements_adapter_protocol(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-bot-token")
    # 64-hex-char Ed25519 public key (content irrelevant for this structural check).
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", "0" * 64)
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789012345678")
    adapter = create_discord_adapter()
    assert isinstance(adapter, Adapter), "DiscordAdapter missing Protocol methods"
