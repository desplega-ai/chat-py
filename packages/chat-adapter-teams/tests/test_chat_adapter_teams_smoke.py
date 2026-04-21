"""Smoke test — ensures the package imports and advertises a version."""

import chat_adapter_teams


def test_module_has_version() -> None:
    assert hasattr(chat_adapter_teams, "__version__")
    assert isinstance(chat_adapter_teams.__version__, str)
