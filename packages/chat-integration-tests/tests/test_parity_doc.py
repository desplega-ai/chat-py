"""Tests that ensure ``docs/parity.md`` documents the adapter dispatch surface.

These tests pin the documentation state that prevents the Slack/GChat
dispatch gap (DES-196) from silently regressing: every adapter must appear
in a dispatch-surface table, and every deliberate ``NotImplementedError``
stub must be enumerated alongside the adapter that owns it.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PARITY = (REPO_ROOT / "docs" / "parity.md").read_text()


def test_parity_lists_dispatch_surface_per_adapter() -> None:
    assert "## Dispatch surface" in PARITY
    for adapter in (
        "slack",
        "gchat",
        "discord",
        "github",
        "teams",
        "linear",
        "telegram",
        "whatsapp",
    ):
        assert adapter in PARITY.lower(), f"dispatch table missing {adapter}"


def test_parity_enumerates_intentional_not_implemented_stubs() -> None:
    assert "### Deliberate NotImplementedError stubs" in PARITY
    assert "chat-adapter-teams" in PARITY
    assert "chat-adapter-whatsapp" in PARITY
    assert "chat-adapter-telegram" in PARITY
