# chat-adapter-whatsapp

WhatsApp (Meta Cloud API) adapter for [`chat-py`](https://github.com/desplega-ai/chat-py). Python port of upstream [`packages/adapter-whatsapp`](https://github.com/vercel/chat/tree/main/packages/adapter-whatsapp).

## Status

**v0.1.0 — placeholder stub.** The WhatsApp port is tracked under Linear issue DES-183 and is not yet implemented; importing `chat_adapter_whatsapp` currently only exposes `__version__`. The top-level support matrix lists WhatsApp as a target platform; concrete support (Meta Cloud API tokens, webhook verification, list / reply-button messages) lands in a follow-up release.

## Install

```bash
uv add chat-adapter-whatsapp
```

## Planned features

- Meta Cloud API token auth (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`)
- `hub.challenge` verification handshake and signed webhook envelope validation
- List messages and reply-button cards (partial card support)
- Reactions (WhatsApp native reaction API)
- Media (image, document, audio, video) uploads via the Cloud API `/media` endpoint

## Test

```bash
# The smoke test asserts only that the placeholder imports cleanly.
uv run pytest packages/chat-adapter-whatsapp
```

## Upstream

https://github.com/vercel/chat/tree/main/packages/adapter-whatsapp
