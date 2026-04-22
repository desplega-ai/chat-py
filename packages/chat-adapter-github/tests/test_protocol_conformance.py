"""GitHub adapter structural conformance against ``chat.types.Adapter``.

Mirrors the Slack / GChat / Discord conformance tests from earlier DES-196
phases. Pins that :class:`GitHubAdapter` satisfies the structural ``Adapter``
Protocol so it can be used interchangeably by :class:`chat.Chat`.
"""

from __future__ import annotations

from chat.types import Adapter
from chat_adapter_github import create_github_adapter


def test_github_adapter_implements_adapter_protocol(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")
    monkeypatch.setenv("GITHUB_BOT_USERNAME", "test-bot")
    adapter = create_github_adapter()
    assert isinstance(adapter, Adapter), "GitHubAdapter missing Protocol methods"
