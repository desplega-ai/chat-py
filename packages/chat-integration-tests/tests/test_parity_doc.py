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


# ---------------------------------------------------------------------------
# Phase 10 — Parity-doc self-test.
#
# Walks the ``packages/`` tree to enumerate every shipped adapter module,
# then asserts every row of the ``## Dispatch surface`` table in
# ``parity.md`` corresponds to a real adapter. Catches stale parity.md
# entries (e.g. an adapter deleted without updating docs, or a row
# typo'd into a name that doesn't exist).
# ---------------------------------------------------------------------------

PACKAGES_DIR = REPO_ROOT / "packages"

# Map adapter row-names (as used in parity.md) to the corresponding
# ``chat-adapter-<row>`` package directory. Keep this list exhaustive —
# adding a new dispatch adapter must both create a package AND add a row.
_DISPATCH_ADAPTERS = (
    "slack",
    "gchat",
    "discord",
    "github",
    "teams",
    "linear",
    "telegram",
    "whatsapp",
)


def _dispatch_table_rows() -> list[str]:
    """Extract the row ids from the ``## Dispatch surface`` Markdown table."""

    section_start = PARITY.find("## Dispatch surface")
    assert section_start >= 0, "Dispatch surface section missing"
    # Rows are of the form ``| name   | ...``. Collect everything until
    # the next top-level ``##`` heading.
    remainder = PARITY[section_start:]
    next_section = remainder.find("\n## ", 3)
    segment = remainder[:next_section] if next_section > 0 else remainder
    rows: list[str] = []
    for line in segment.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        if cells[0].lower() in {"adapter", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells[0].lower())
    return rows


def test_every_parity_dispatch_row_has_a_real_adapter_package() -> None:
    """Every row in the dispatch-surface table must correspond to a shipped adapter package."""

    rows = _dispatch_table_rows()
    assert rows, "No dispatch-surface rows parsed — table format drifted?"
    for row in rows:
        pkg_dir = PACKAGES_DIR / f"chat-adapter-{row}"
        assert pkg_dir.is_dir(), (
            f"parity.md lists '{row}' but packages/chat-adapter-{row} does not exist"
        )
        src_dir = pkg_dir / "src" / f"chat_adapter_{row}"
        assert src_dir.is_dir(), (
            f"parity.md lists '{row}' but {src_dir.relative_to(REPO_ROOT)} is missing"
        )


def test_every_real_adapter_has_a_parity_dispatch_row() -> None:
    """Every shipped dispatch adapter must appear in the parity table (no stragglers)."""

    rows = set(_dispatch_table_rows())
    for adapter in _DISPATCH_ADAPTERS:
        assert adapter in rows, (
            f"chat-adapter-{adapter} ships a handle_webhook but parity.md "
            "has no Dispatch surface row for it"
        )
