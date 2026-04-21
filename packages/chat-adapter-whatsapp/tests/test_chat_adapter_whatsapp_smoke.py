"""Smoke test — ensures the package imports and advertises a version."""

import chat_adapter_whatsapp


def test_module_has_version() -> None:
    assert hasattr(chat_adapter_whatsapp, "__version__")
    assert isinstance(chat_adapter_whatsapp.__version__, str)
