"""Smoke test — ensures the package imports and advertises a version."""

import chat


def test_module_has_version() -> None:
    assert hasattr(chat, "__version__")
    assert isinstance(chat.__version__, str)
