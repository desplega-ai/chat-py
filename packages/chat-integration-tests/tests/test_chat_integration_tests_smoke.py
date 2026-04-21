"""Smoke test — ensures the package imports and advertises a version."""

import chat_integration_tests


def test_module_has_version() -> None:
    assert hasattr(chat_integration_tests, "__version__")
    assert isinstance(chat_integration_tests.__version__, str)
