"""Phase 5 dispatch tests for :class:`GitHubAdapter`.

Pins that :meth:`chat.Chat.handle_webhook` round-trips a real GitHub
``issue_comment`` payload all the way to a registered :func:`on_new_mention`
handler, proving ``initialize(chat)`` wires ``self._chat`` correctly for the
shared dispatch surface.

The HMAC-SHA256 signature over the body is computed here (no extra deps
beyond stdlib ``hmac`` / ``hashlib``) so we exercise the real
``verify_github_signature`` path — not a monkeypatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state
from chat_adapter_github import create_github_adapter
from chat_adapter_github.adapter import GitHubAdapter

WEBHOOK_SECRET = "webhook-test-secret-DES-196-phase-5"
BOT_USERNAME = "my-bot"


def _sign(body: bytes) -> str:
    """Compute a GitHub ``X-Hub-Signature-256`` value for ``body``."""

    digest = hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> GitHubAdapter:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")
    monkeypatch.setenv("GITHUB_BOT_USERNAME", BOT_USERNAME)
    return create_github_adapter()


async def test_issue_comment_fires_mention_via_chat_handle_webhook(
    adapter: GitHubAdapter,
) -> None:
    """Round-trip: issue_comment.created → Chat.handle_webhook → on_new_mention."""

    bot = Chat(user_name=BOT_USERNAME, adapters={"github": adapter}, state=create_mock_state())
    seen = asyncio.Event()
    captured: dict[str, Any] = {}

    async def handler(thread: Any, message: Any, _context: Any = None) -> None:
        captured["text"] = message.text
        captured["thread_id"] = thread.id if hasattr(thread, "id") else None
        captured["author_user_name"] = message.author.user_name
        seen.set()

    bot.on_new_mention(handler)

    payload = {
        "action": "created",
        "issue": {
            "number": 42,
            # Presence of ``pull_request`` keys the thread as type="pr" — omit
            # here so the comment is on an issue (thread type="issue").
            "title": "Sample issue",
        },
        "comment": {
            "id": 987654321,
            "body": f"@{BOT_USERNAME} please echo this",
            "user": {
                "id": 12345,
                "login": "alice",
                "type": "User",
            },
            "created_at": "2026-04-22T12:00:00Z",
            "updated_at": "2026-04-22T12:00:00Z",
        },
        "repository": {
            "id": 1,
            "name": "chat-py",
            "full_name": "desplega-ai/chat-py",
            "owner": {"id": 9, "login": "desplega-ai", "type": "Organization"},
        },
        "sender": {"id": 12345, "login": "alice", "type": "User"},
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "x-hub-signature-256": _sign(body),
        "x-github-event": "issue_comment",
        "x-github-delivery": str(uuid.uuid4()),
        "content-type": "application/json",
    }

    status, _resp_headers, resp_body = await bot.handle_webhook("github", body, headers)
    assert status == 200
    assert resp_body == "ok"

    await asyncio.wait_for(seen.wait(), timeout=2.0)
    assert f"@{BOT_USERNAME}" in captured["text"]
    assert captured["author_user_name"] == "alice"
    assert captured["thread_id"] is not None


async def test_invalid_signature_returns_401(adapter: GitHubAdapter) -> None:
    """Tampered body → ``verify_github_signature`` rejects → 401."""

    payload = {"action": "created", "zen": "signature mismatch test"}
    body = json.dumps(payload).encode("utf-8")
    # Sign a *different* body so the header is valid-shaped but wrong.
    headers = {
        "x-hub-signature-256": _sign(body + b"-tampered"),
        "x-github-event": "issue_comment",
        "content-type": "application/json",
    }
    status, _headers, _body = await adapter.handle_webhook(body, headers)
    assert status == 401


async def test_initialize_stores_chat_reference(adapter: GitHubAdapter) -> None:
    """Confirm ``initialize(chat)`` wires ``self._chat`` correctly.

    Mirrors the Discord phase-4 guard that caught the wiring bug elsewhere.
    """

    sentinel = object()
    # Bypass the ``/user`` REST probe that ``initialize`` normally does.
    # We can't await a sentinel, so we only care that ``self._chat`` is set.
    adapter._bot_user_id = 1  # skip the REST lookup branch
    await adapter.initialize(sentinel)
    assert adapter._chat is sentinel
