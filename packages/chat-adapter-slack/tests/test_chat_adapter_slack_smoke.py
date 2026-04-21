"""Smoke test — ensures the package imports and advertises a version."""

import chat_adapter_slack


def test_module_has_version() -> None:
    assert hasattr(chat_adapter_slack, "__version__")
    assert isinstance(chat_adapter_slack.__version__, str)
