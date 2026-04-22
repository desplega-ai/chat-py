# chat-adapter-shared

Shared helpers used by every `chat-adapter-*` package in [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-shared`](https://github.com/vercel/chat/tree/main/packages/adapter-shared).

You only depend on this package directly if you're implementing a new adapter. Normal users pull it in transitively through `chat-adapter-slack`, `chat-adapter-teams`, etc.

## Install

```bash
uv add chat-adapter-shared
```

## What it ships

- `adapter_utils` — `extract_card`, `extract_files` (pull card / file payloads out of a message).
- `buffer_utils` — `to_buffer`, `to_buffer_sync`, `buffer_to_data_uri` for fetching and normalizing file inputs (bytes, paths, URLs, data URIs).
- `card_utils` — `card_to_fallback_text` (plain-text rendering of a card), `map_button_style`, `render_gfm_table`, `create_emoji_converter`.
- `errors` — shared error hierarchy: `AdapterError`, `AdapterRateLimitError`, `AuthenticationError`, `NetworkError`, `PermissionError`, `ResourceNotFoundError`, `ValidationError`.

## Parity notes

- 1:1 port of upstream helpers; function names and semantics match.

## Test

```bash
uv run pytest packages/chat-adapter-shared
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-shared
