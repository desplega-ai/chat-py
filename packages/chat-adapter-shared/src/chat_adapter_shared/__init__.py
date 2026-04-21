"""Shared utilities for chat-py platform adapters.

Python port of upstream ``packages/adapter-shared``. Re-exports the public
API from the four submodules (``adapter_utils``, ``buffer_utils``,
``card_utils``, ``errors``).
"""

from chat_adapter_shared.adapter_utils import extract_card, extract_files
from chat_adapter_shared.buffer_utils import (
    FileDataInput,
    ToBufferOptions,
    buffer_to_data_uri,
    to_buffer,
    to_buffer_sync,
)
from chat_adapter_shared.card_utils import (
    BUTTON_STYLE_MAPPINGS,
    FallbackTextOptions,
    PlatformName,
    card_to_fallback_text,
    create_emoji_converter,
    escape_table_cell,
    map_button_style,
    render_gfm_table,
)
from chat_adapter_shared.errors import (
    AdapterError,
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ResourceNotFoundError,
    ValidationError,
)

__version__ = "0.1.0"

__all__ = [
    "BUTTON_STYLE_MAPPINGS",
    "AdapterError",
    "AdapterRateLimitError",
    "AuthenticationError",
    "FallbackTextOptions",
    "FileDataInput",
    "NetworkError",
    "PermissionError",
    "PlatformName",
    "ResourceNotFoundError",
    "ToBufferOptions",
    "ValidationError",
    "__version__",
    "buffer_to_data_uri",
    "card_to_fallback_text",
    "create_emoji_converter",
    "escape_table_cell",
    "extract_card",
    "extract_files",
    "map_button_style",
    "render_gfm_table",
    "to_buffer",
    "to_buffer_sync",
]
