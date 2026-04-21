"""Smoke test — ensures the package imports and advertises a version."""

import chat_adapter_state_ioredis


def test_module_has_version() -> None:
    assert hasattr(chat_adapter_state_ioredis, "__version__")
    assert isinstance(chat_adapter_state_ioredis.__version__, str)
