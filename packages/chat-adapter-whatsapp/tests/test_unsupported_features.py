"""Pin WhatsApp adapter's deliberate ``chat.NotImplementedError`` stubs.

WhatsApp Cloud API does not support:

- Editing messages (``edit_message``)
- Deleting messages (``delete_message``)

and is 1:1 DM-only, so channel-surface methods and modals are also stubbed.
Each site raises :class:`chat.errors.NotImplementedError` (the SDK-specific
error class, *not* the Python builtin) with a ``feature`` attribute so
callers can react programmatically.

See ``docs/parity.md`` → "Deliberate NotImplementedError stubs" for the
matching documentation.
"""

from __future__ import annotations

import builtins

import pytest
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat_adapter_whatsapp import create_whatsapp_adapter
from chat_adapter_whatsapp.adapter import WhatsAppAdapter


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> WhatsAppAdapter:
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    return create_whatsapp_adapter()


async def test_edit_message_raises_chat_not_implemented(adapter: WhatsAppAdapter) -> None:
    """WhatsApp Cloud API has no edit endpoint."""

    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.edit_message("whatsapp:123:456", "wamid.X", object())
    assert exc_info.value.feature == "editMessage"
    # Assert the error is the SDK type, not Python's builtin NotImplementedError.
    assert not isinstance(exc_info.value, builtins.NotImplementedError)
    assert isinstance(exc_info.value, ChatNotImplementedError)


async def test_delete_message_raises_chat_not_implemented(
    adapter: WhatsAppAdapter,
) -> None:
    """WhatsApp Cloud API has no delete endpoint."""

    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.delete_message("whatsapp:123:456", "wamid.X")
    assert exc_info.value.feature == "deleteMessage"
    assert isinstance(exc_info.value, ChatNotImplementedError)


# The remaining stubs below exist purely to satisfy the ``chat.types.Adapter``
# Protocol (WhatsApp is DM-only, no channels / modals). They are still pinned
# so future "helpful" re-implementations can't silently wander off parity.


async def test_post_channel_message_raises_chat_not_implemented(
    adapter: WhatsAppAdapter,
) -> None:
    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.post_channel_message("whatsapp:channel", object())
    assert exc_info.value.feature == "post_channel_message"


async def test_fetch_channel_info_raises_chat_not_implemented(
    adapter: WhatsAppAdapter,
) -> None:
    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.fetch_channel_info("whatsapp:channel")
    assert exc_info.value.feature == "fetch_channel_info"


async def test_fetch_channel_messages_raises_chat_not_implemented(
    adapter: WhatsAppAdapter,
) -> None:
    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.fetch_channel_messages("whatsapp:channel")
    assert exc_info.value.feature == "fetch_channel_messages"


async def test_list_threads_raises_chat_not_implemented(adapter: WhatsAppAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.list_threads("whatsapp:channel")
    assert exc_info.value.feature == "list_threads"


async def test_open_modal_raises_chat_not_implemented(adapter: WhatsAppAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc_info:
        await adapter.open_modal("trigger-id", object())
    assert exc_info.value.feature == "open_modal"
