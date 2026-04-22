"""Phase 8 dispatch tests for :class:`LinearAdapter`.

Pins that :meth:`chat.Chat.handle_webhook` round-trips a real Linear
``Comment`` payload all the way to a registered :func:`on_new_mention`
handler, proving ``initialize(chat)`` wires ``self._chat`` correctly for
the shared dispatch surface.

Also audits:
- The HMAC-SHA256 ``Linear-Signature`` path is exercised for real (no
  monkeypatch of ``verify_linear_signature``).
- The ``comment.user`` payload is serialised into an :class:`Author`
  dataclass (not a dict) — guarding against the "author dict" regression
  observed in other adapters where ``message.author`` was left as a dict
  and downstream ``message.author.user_name`` access crashed.
- :meth:`LinearAdapter.remove_reaction` raises
  :class:`chat.errors.NotImplementedError` with ``feature="removeReaction"``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from chat import Chat
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat.mock_adapter import create_mock_state
from chat_adapter_linear import create_linear_adapter, verify_linear_signature
from chat_adapter_linear.adapter import LinearAdapter

WEBHOOK_SECRET = "webhook-test-secret-DES-196-phase-8"
BOT_USERNAME = "chat-py-bot"


def _sign(body: bytes) -> str:
    """Compute a Linear ``Linear-Signature`` value for ``body``.

    Linear uses plain HMAC-SHA256 hex (no ``sha256=`` prefix).
    """

    import hashlib
    import hmac

    return hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> LinearAdapter:
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("LINEAR_BOT_USERNAME", BOT_USERNAME)
    return create_linear_adapter()


async def test_comment_created_fires_mention_via_chat_handle_webhook(
    adapter: LinearAdapter,
) -> None:
    """Round-trip: Comment webhook → Chat.handle_webhook → on_new_mention."""

    # Pre-populate viewer identity so ``initialize`` skips the outbound
    # GraphQL probe (no httpx mock needed for the dispatch round-trip).
    adapter._default_bot_user_id = "bot-user-id-123"
    adapter._default_organization_id = "org-abc"

    async def _fake_identity(_token: str) -> dict[str, str]:
        return {
            "botUserId": "bot-user-id-123",
            "organizationId": "org-abc",
            "displayName": BOT_USERNAME,
        }

    adapter._fetch_viewer_identity = _fake_identity  # type: ignore[assignment]

    bot = Chat(
        user_name=BOT_USERNAME,
        adapters={"linear": adapter},
        state=create_mock_state(),
    )
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, _context: Any = None) -> None:
        captured["text"] = message.text
        captured["thread_id"] = getattr(thread, "id", None)
        # Author-dict bug guard: .user_name must be attribute access, not
        # dict[key]. This raises if the adapter left author as a dict.
        captured["author_user_name"] = message.author.user_name
        captured["author_full_name"] = message.author.full_name
        seen.set()

    bot.on_new_mention(handler)

    payload = {
        "action": "create",
        "type": "Comment",
        "organizationId": "org-abc",
        "url": "https://linear.app/acme/issue/ENG-42#comment-99",
        "data": {
            "id": "comment-99",
            "body": f"@{BOT_USERNAME} please echo this",
            "issueId": "issue-ENG-42",
            "parentId": None,
            "createdAt": "2026-04-22T12:00:00.000Z",
            "updatedAt": "2026-04-22T12:00:00.000Z",
            "user": {
                "id": "user-alice",
                "name": "Alice Example",
                "email": "alice@example.com",
                "url": "https://linear.app/acme/profiles/alice",
                "avatarUrl": None,
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "linear-signature": _sign(body),
        "content-type": "application/json",
    }

    status, _resp_headers, resp_body = await bot.handle_webhook("linear", body, headers)
    assert status == 200
    assert resp_body == "ok"

    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert f"@{BOT_USERNAME}" in captured["text"]
    # ``user_name`` is derived from the profile URL slug — ``alice`` here.
    assert captured["author_user_name"] == "alice"
    assert captured["author_full_name"] == "Alice Example"
    assert captured["thread_id"] is not None


async def test_invalid_signature_returns_401(adapter: LinearAdapter) -> None:
    """Tampered body → ``verify_linear_signature`` rejects → 401."""

    payload = {"type": "Comment", "action": "create", "data": {}}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        # Sign a *different* body so the header is valid-shaped but wrong.
        "linear-signature": _sign(body + b"-tampered"),
        "content-type": "application/json",
    }
    status, _headers, _body = await adapter.handle_webhook(body, headers)
    assert status == 401


async def test_initialize_stores_chat_reference(adapter: LinearAdapter) -> None:
    """Confirm ``initialize(chat)`` wires ``self._chat`` for dispatch.

    We set ``_default_bot_user_id`` up front so ``initialize`` skips the
    outbound viewer-identity GraphQL probe.
    """

    adapter._default_bot_user_id = "bot-user-id"
    adapter._default_organization_id = "org-abc"
    # Monkey the fetch to avoid real HTTP.
    adapter._fetch_viewer_identity = lambda _token: {  # type: ignore[assignment]
        "botUserId": "bot-user-id",
        "organizationId": "org-abc",
        "displayName": BOT_USERNAME,
    }

    sentinel = object()
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel


async def test_remove_reaction_raises_chat_not_implemented(
    adapter: LinearAdapter,
) -> None:
    """Pin that ``remove_reaction`` raises ``chat.NotImplementedError``."""

    with pytest.raises(ChatNotImplementedError) as excinfo:
        await adapter.remove_reaction("linear:issue:c:comment", "comment", "👍")
    assert excinfo.value.feature == "removeReaction"


def test_verify_linear_signature_accepts_valid_sig() -> None:
    body = b'{"type":"Comment"}'
    sig = _sign(body)
    assert verify_linear_signature(WEBHOOK_SECRET, sig, body) is True
    assert verify_linear_signature(WEBHOOK_SECRET, sig, body + b"x") is False
