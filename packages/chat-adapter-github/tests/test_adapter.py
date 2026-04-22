"""Tests for :class:`GitHubAdapter` and :func:`verify_github_signature`.

Mirrors the structure of upstream ``packages/adapter-github/src/index.test.ts``
while using Python-native fixtures (``respx`` for httpx mocking, ``cryptography``
for RSA key generation).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from chat_adapter_github.adapter import (
    GITHUB_API_BASE,
    GitHubAdapter,
    create_github_adapter,
    verify_github_signature,
)
from chat_adapter_shared import ValidationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

WEBHOOK_SECRET = "test-secret"
_ENV_KEYS = (
    "GITHUB_TOKEN",
    "GITHUB_APP_ID",
    "GITHUB_PRIVATE_KEY",
    "GITHUB_INSTALLATION_ID",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_BOT_USERNAME",
    "GITHUB_API_URL",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="module")
def rsa_private_key_pem() -> str:
    """Generate a throw-away RSA key pair for tests."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: str | bytes, secret: str = WEBHOOK_SECRET) -> str:
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_issue_comment_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "created",
        "comment": {
            "id": 100,
            "body": "Hello from test",
            "user": {"id": 1, "login": "testuser", "type": "User"},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/acme/app/pull/42#issuecomment-100",
        },
        "issue": {
            "number": 42,
            "title": "Test PR",
            "pull_request": {"url": "https://api.github.com/repos/acme/app/pulls/42"},
        },
        "repository": {
            "id": 1,
            "name": "app",
            "full_name": "acme/app",
            "owner": {"id": 10, "login": "acme", "type": "Organization"},
        },
        "sender": {"id": 1, "login": "testuser", "type": "User"},
    }
    payload.update(overrides)
    return payload


def _make_review_comment_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "created",
        "comment": {
            "id": 200,
            "body": "Review comment text",
            "user": {"id": 2, "login": "reviewer", "type": "User"},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/acme/app/pull/42#discussion_r200",
            "path": "src/index.ts",
            "diff_hunk": "@@ -1,3 +1,4 @@",
            "commit_id": "abc123",
            "original_commit_id": "abc123",
        },
        "pull_request": {
            "id": 500,
            "number": 42,
            "title": "Test PR",
            "state": "open",
            "body": "PR body",
            "html_url": "https://github.com/acme/app/pull/42",
            "user": {"id": 10, "login": "acme", "type": "Organization"},
        },
        "repository": {
            "id": 1,
            "name": "app",
            "full_name": "acme/app",
            "owner": {"id": 10, "login": "acme", "type": "Organization"},
        },
        "sender": {"id": 2, "login": "reviewer", "type": "User"},
    }
    payload.update(overrides)
    return payload


def _make_adapter(**overrides: Any) -> GitHubAdapter:
    config: dict[str, Any] = {
        "token": "test-token",
        "webhookSecret": WEBHOOK_SECRET,
        "userName": "test-bot",
    }
    config.update(overrides)
    return GitHubAdapter(config)  # type: ignore[arg-type]


def _make_mock_state() -> Any:
    cache: dict[str, Any] = {}
    state = MagicMock()
    state.get = AsyncMock(side_effect=lambda key: cache.get(key))
    state.set = AsyncMock(side_effect=lambda key, value: cache.__setitem__(key, value))
    return state


def _make_mock_chat(state: Any = None) -> Any:
    chat = MagicMock()
    chat.get_state = MagicMock(return_value=state)
    chat.process_message = AsyncMock()
    return chat


# ---------------------------------------------------------------------------
# verify_github_signature
# ---------------------------------------------------------------------------


class TestVerifyGitHubSignature:
    def test_accepts_a_valid_signature(self) -> None:
        body = b'{"hello":"world"}'
        sig = _sign(body)
        assert verify_github_signature(WEBHOOK_SECRET, sig, body) is True

    def test_rejects_a_tampered_body(self) -> None:
        sig = _sign(b'{"hello":"world"}')
        assert verify_github_signature(WEBHOOK_SECRET, sig, b'{"hello":"moon"}') is False

    def test_rejects_missing_signature(self) -> None:
        assert verify_github_signature(WEBHOOK_SECRET, None, b"anything") is False
        assert verify_github_signature(WEBHOOK_SECRET, "", b"anything") is False

    def test_rejects_a_bad_prefix(self) -> None:
        assert verify_github_signature(WEBHOOK_SECRET, "sha1=deadbeef", b"body") is False

    def test_accepts_string_body(self) -> None:
        body = '{"hello":"world"}'
        sig = _sign(body)
        assert verify_github_signature(WEBHOOK_SECRET, sig, body) is True


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_creates_adapter_with_pat_config(self) -> None:
        a = _make_adapter(token="ghp_abc", userName="bot")
        assert a.name == "github"
        assert a.user_name == "bot"
        assert a.is_multi_tenant is False

    def test_creates_adapter_with_app_plus_installation_id(self, rsa_private_key_pem: str) -> None:
        a = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "installationId": 99,
                "webhookSecret": "secret",
                "userName": "my-bot[bot]",
            }
        )
        assert a.is_multi_tenant is False

    def test_creates_adapter_in_multi_tenant_mode(self, rsa_private_key_pem: str) -> None:
        a = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "webhookSecret": "secret",
                "userName": "my-bot[bot]",
            }
        )
        assert a.is_multi_tenant is True

    def test_requires_webhook_secret(self) -> None:
        with pytest.raises(ValidationError):
            GitHubAdapter({"token": "ghp_abc", "userName": "bot"})

    def test_requires_auth(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            GitHubAdapter({"webhookSecret": "secret", "userName": "bot"})
        assert "Authentication" in str(excinfo.value)

    def test_sets_bot_user_id(self) -> None:
        a = _make_adapter(botUserId=42)
        assert a.bot_user_id == "42"

    def test_bot_user_id_defaults_to_none(self) -> None:
        assert _make_adapter().bot_user_id is None


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------


class TestHandleWebhook:
    @pytest.mark.asyncio
    async def test_returns_401_for_missing_signature(self) -> None:
        adapter = _make_adapter()
        body = json.dumps(_make_issue_comment_payload())
        status, _, response_body = await adapter.handle_webhook(
            body,
            {"x-github-event": "issue_comment", "content-type": "application/json"},
        )
        assert status == 401
        assert response_body == "Invalid signature"

    @pytest.mark.asyncio
    async def test_returns_401_for_invalid_signature(self) -> None:
        adapter = _make_adapter()
        body = json.dumps(_make_issue_comment_payload())
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": "sha256=invalid",
                "content-type": "application/json",
            },
        )
        assert status == 401

    @pytest.mark.asyncio
    async def test_returns_200_pong_for_ping_event(self) -> None:
        adapter = _make_adapter()
        body = json.dumps({"zen": "test"})
        status, _, response_body = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "ping",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        assert response_body == "pong"

    @pytest.mark.asyncio
    async def test_returns_400_for_invalid_json(self) -> None:
        adapter = _make_adapter()
        body = "not-json{{{"
        status, _, response_body = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 400
        assert "Invalid JSON" in response_body

    @pytest.mark.asyncio
    async def test_processes_issue_comment_on_pr(self) -> None:
        adapter = _make_adapter()
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        body = json.dumps(_make_issue_comment_payload())
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_awaited_once()
        args = chat.process_message.await_args.args
        assert args[0] is adapter
        assert args[1] == "github:acme/app:42"
        assert args[2].id == "100"

    @pytest.mark.asyncio
    async def test_processes_issue_comment_on_plain_issue(self) -> None:
        adapter = _make_adapter()
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        payload = _make_issue_comment_payload(
            issue={"number": 10, "title": "Bug", "pull_request": None}
        )
        body = json.dumps(payload)
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_awaited_once()
        args = chat.process_message.await_args.args
        assert args[1] == "github:acme/app:issue:10"
        assert args[2].thread_id == "github:acme/app:issue:10"

    @pytest.mark.asyncio
    async def test_ignores_issue_comment_with_non_created_action(self) -> None:
        adapter = _make_adapter()
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        payload = _make_issue_comment_payload(action="edited")
        body = json.dumps(payload)
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_review_comment(self) -> None:
        adapter = _make_adapter()
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        body = json.dumps(_make_review_comment_payload())
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "pull_request_review_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_awaited_once()
        args = chat.process_message.await_args.args
        assert args[1] == "github:acme/app:42:rc:200"

    @pytest.mark.asyncio
    async def test_uses_in_reply_to_id_as_root_for_review_replies(self) -> None:
        adapter = _make_adapter()
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        payload = _make_review_comment_payload()
        payload["comment"]["id"] = 201
        payload["comment"]["in_reply_to_id"] = 200
        body = json.dumps(payload)
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "pull_request_review_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        args = chat.process_message.await_args.args
        assert args[1] == "github:acme/app:42:rc:200"

    @pytest.mark.asyncio
    async def test_ignores_review_comment_with_non_created_action(self) -> None:
        adapter = _make_adapter()
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        payload = _make_review_comment_payload(action="edited")
        body = json.dumps(payload)
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "pull_request_review_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_ok_for_unrecognized_events(self) -> None:
        adapter = _make_adapter()
        body = json.dumps({"action": "foo"})
        status, _, response_body = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "star",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        assert response_body == "ok"

    @pytest.mark.asyncio
    async def test_ignores_self_messages_issue_comment(self) -> None:
        adapter = _make_adapter(botUserId=1)
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        body = json.dumps(_make_issue_comment_payload())
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_self_messages_review_comment(self) -> None:
        adapter = _make_adapter(botUserId=2)
        chat = _make_mock_chat()
        await adapter.initialize(chat)

        body = json.dumps(_make_review_comment_payload())
        status, _, _ = await adapter.handle_webhook(
            body,
            {
                "x-github-event": "pull_request_review_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )
        assert status == 200
        chat.process_message.assert_not_called()


# ---------------------------------------------------------------------------
# get_installation_id / multi-tenant
# ---------------------------------------------------------------------------


class TestGetInstallationId:
    @pytest.mark.asyncio
    async def test_returns_fixed_installation_in_single_tenant(
        self, rsa_private_key_pem: str
    ) -> None:
        adapter = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "installationId": 456,
                "webhookSecret": WEBHOOK_SECRET,
                "userName": "test-bot[bot]",
            }
        )
        assert await adapter.get_installation_id("github:acme/app:42") == 456

    @pytest.mark.asyncio
    async def test_returns_none_in_pat_mode(self) -> None:
        assert await _make_adapter().get_installation_id("github:acme/app:42") is None

    @pytest.mark.asyncio
    async def test_caches_installation_after_webhook(self, rsa_private_key_pem: str) -> None:
        adapter = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "webhookSecret": WEBHOOK_SECRET,
                "userName": "test-bot[bot]",
            }
        )
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        await adapter.initialize(chat)

        payload = _make_issue_comment_payload(installation={"id": 789})
        body = json.dumps(payload)
        await adapter.handle_webhook(
            body,
            {
                "x-github-event": "issue_comment",
                "x-hub-signature-256": _sign(body),
                "content-type": "application/json",
            },
        )

        assert await adapter.get_installation_id("github:acme/app:42") == 789

    @pytest.mark.asyncio
    async def test_returns_none_when_not_cached(self, rsa_private_key_pem: str) -> None:
        adapter = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "webhookSecret": WEBHOOK_SECRET,
                "userName": "test-bot[bot]",
            }
        )
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        await adapter.initialize(chat)

        assert await adapter.get_installation_id("github:acme/app:42") is None

    @pytest.mark.asyncio
    async def test_rejects_foreign_thread_id(self, rsa_private_key_pem: str) -> None:
        adapter = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "webhookSecret": WEBHOOK_SECRET,
                "userName": "test-bot[bot]",
            }
        )
        with pytest.raises(ValidationError):
            await adapter.get_installation_id("slack:C123:1234.5678")

    @pytest.mark.asyncio
    async def test_rejects_call_before_initialization(self, rsa_private_key_pem: str) -> None:
        adapter = GitHubAdapter(
            {
                "appId": "12345",
                "privateKey": rsa_private_key_pem,
                "webhookSecret": WEBHOOK_SECRET,
                "userName": "test-bot[bot]",
            }
        )
        with pytest.raises(ValidationError) as excinfo:
            await adapter.get_installation_id("github:acme/app:42")
        assert "not initialized" in str(excinfo.value)


# ---------------------------------------------------------------------------
# post_message / edit_message / delete_message
# ---------------------------------------------------------------------------


class TestPostMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_issue_comment_for_pr_thread(self) -> None:
        adapter = _make_adapter()
        route = respx.post(f"{GITHUB_API_BASE}/repos/acme/app/issues/42/comments").mock(
            return_value=httpx.Response(201, json={"id": 999, "body": "hi"})
        )

        result = await adapter.post_message("github:acme/app:42", "hi")
        assert route.called
        assert result["id"] == "999"
        assert result["threadId"] == "github:acme/app:42"

    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_review_comment_reply(self) -> None:
        adapter = _make_adapter()
        route = respx.post(f"{GITHUB_API_BASE}/repos/acme/app/pulls/42/comments/200/replies").mock(
            return_value=httpx.Response(201, json={"id": 5555, "body": "reply"})
        )

        result = await adapter.post_message("github:acme/app:42:rc:200", "reply")
        assert route.called
        assert result["id"] == "5555"

    @pytest.mark.asyncio
    @respx.mock
    async def test_renders_card_messages_to_markdown(self) -> None:
        adapter = _make_adapter()
        captured: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        respx.post(f"{GITHUB_API_BASE}/repos/acme/app/issues/42/comments").mock(side_effect=capture)

        card = {
            "type": "card",
            "title": "Notification",
            "children": [{"type": "text", "content": "Done"}],
        }
        await adapter.post_message("github:acme/app:42", card)
        assert "**Notification**" in captured["body"]["body"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_with_ast_message_format(self) -> None:
        adapter = _make_adapter()
        captured: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        respx.post(f"{GITHUB_API_BASE}/repos/acme/app/issues/42/comments").mock(side_effect=capture)

        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "Hello world"}],
                },
            ],
        }
        await adapter.post_message("github:acme/app:42", {"ast": ast})
        assert captured["body"]["body"].strip() == "Hello world"


class TestEditMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_edits_issue_comment(self) -> None:
        adapter = _make_adapter()
        route = respx.patch(f"{GITHUB_API_BASE}/repos/acme/app/issues/comments/100").mock(
            return_value=httpx.Response(200, json={"id": 100, "body": "updated"})
        )

        await adapter.edit_message("github:acme/app:42", "100", "updated")
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_edits_review_comment(self) -> None:
        adapter = _make_adapter()
        route = respx.patch(f"{GITHUB_API_BASE}/repos/acme/app/pulls/comments/200").mock(
            return_value=httpx.Response(200, json={"id": 200, "body": "updated"})
        )

        await adapter.edit_message("github:acme/app:42:rc:200", "200", "updated")
        assert route.called


class TestDeleteMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_deletes_issue_comment(self) -> None:
        adapter = _make_adapter()
        route = respx.delete(f"{GITHUB_API_BASE}/repos/acme/app/issues/comments/100").mock(
            return_value=httpx.Response(204)
        )

        await adapter.delete_message("github:acme/app:42", "100")
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_deletes_review_comment(self) -> None:
        adapter = _make_adapter()
        route = respx.delete(f"{GITHUB_API_BASE}/repos/acme/app/pulls/comments/200").mock(
            return_value=httpx.Response(204)
        )

        await adapter.delete_message("github:acme/app:42:rc:200", "200")
        assert route.called


# ---------------------------------------------------------------------------
# stream
# ---------------------------------------------------------------------------


class TestStream:
    @pytest.mark.asyncio
    @respx.mock
    async def test_accumulates_chunks_and_posts_once(self) -> None:
        adapter = _make_adapter()
        captured: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured.setdefault("calls", 0)
            captured["calls"] += 1
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        respx.post(f"{GITHUB_API_BASE}/repos/acme/app/issues/42/comments").mock(side_effect=capture)

        async def chunks() -> Any:
            yield "Hello "
            yield "world"

        await adapter.stream("github:acme/app:42", chunks())
        assert captured["calls"] == 1
        assert "Hello world" in captured["body"]["body"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_stream_chunk_objects(self) -> None:
        adapter = _make_adapter()
        captured: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1})

        respx.post(f"{GITHUB_API_BASE}/repos/acme/app/issues/42/comments").mock(side_effect=capture)

        async def chunks() -> Any:
            yield {"type": "markdown_text", "text": "Hello "}
            yield "world"

        await adapter.stream("github:acme/app:42", chunks())
        assert "Hello world" in captured["body"]["body"]


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


class TestReactions:
    @pytest.mark.asyncio
    @respx.mock
    async def test_add_reaction_for_issue_comment(self) -> None:
        adapter = _make_adapter()
        captured: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 1, "content": "+1"})

        respx.post(f"{GITHUB_API_BASE}/repos/acme/app/issues/comments/100/reactions").mock(
            side_effect=capture
        )

        await adapter.add_reaction("github:acme/app:42", "100", "thumbs_up")
        assert captured["body"] == {"content": "+1"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_reaction_for_review_comment(self) -> None:
        adapter = _make_adapter()
        route = respx.post(f"{GITHUB_API_BASE}/repos/acme/app/pulls/comments/200/reactions").mock(
            return_value=httpx.Response(201, json={"id": 1, "content": "heart"})
        )

        await adapter.add_reaction("github:acme/app:42:rc:200", "200", "heart")
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_remove_reaction_skips_when_no_match(self) -> None:
        adapter = _make_adapter(botUserId=777)
        respx.get(f"{GITHUB_API_BASE}/repos/acme/app/issues/comments/100/reactions").mock(
            return_value=httpx.Response(200, json=[])
        )

        await adapter.remove_reaction("github:acme/app:42", "100", "thumbs_up")

    @pytest.mark.asyncio
    @respx.mock
    async def test_remove_reaction_deletes_matching_bot_reaction(self) -> None:
        adapter = _make_adapter(botUserId=777)
        list_route = respx.get(
            f"{GITHUB_API_BASE}/repos/acme/app/issues/comments/100/reactions"
        ).mock(
            return_value=httpx.Response(
                200, json=[{"id": 42, "content": "+1", "user": {"id": 777}}]
            )
        )
        delete_route = respx.delete(
            f"{GITHUB_API_BASE}/repos/acme/app/issues/comments/100/reactions/42"
        ).mock(return_value=httpx.Response(204))

        await adapter.remove_reaction("github:acme/app:42", "100", "thumbs_up")
        assert list_route.called
        assert delete_route.called


# ---------------------------------------------------------------------------
# fetch_messages / fetch_thread / list_threads / fetch_channel_info
# ---------------------------------------------------------------------------


class TestFetchMessages:
    @pytest.mark.asyncio
    @respx.mock
    async def test_fetches_issue_comments_for_pr_thread(self) -> None:
        adapter = _make_adapter()
        respx.get(
            re.compile(rf"{re.escape(GITHUB_API_BASE)}/repos/acme/app/issues/42/comments.*")
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "body": "First",
                        "user": {"id": 1, "login": "a", "type": "User"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "body": "Second",
                        "user": {"id": 2, "login": "b", "type": "User"},
                        "created_at": "2024-01-02T00:00:00Z",
                        "updated_at": "2024-01-02T00:00:00Z",
                    },
                ],
            )
        )
        result = await adapter.fetch_messages("github:acme/app:42")
        assert len(result["messages"]) == 2
        assert result["messages"][0].id == "1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetches_review_comments_filtered_by_thread(self) -> None:
        adapter = _make_adapter()
        respx.get(
            re.compile(rf"{re.escape(GITHUB_API_BASE)}/repos/acme/app/pulls/42/comments.*")
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 200,
                        "body": "Root",
                        "user": {"id": 2, "login": "rev", "type": "User"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    },
                    {
                        "id": 201,
                        "body": "Reply",
                        "in_reply_to_id": 200,
                        "user": {"id": 3, "login": "rep", "type": "User"},
                        "created_at": "2024-01-02T00:00:00Z",
                        "updated_at": "2024-01-02T00:00:00Z",
                    },
                    {
                        "id": 300,
                        "body": "Other thread",
                        "user": {"id": 4, "login": "oth", "type": "User"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    },
                ],
            )
        )
        result = await adapter.fetch_messages("github:acme/app:42:rc:200")
        ids = {m.id for m in result["messages"]}
        assert ids == {"200", "201"}


class TestFetchThread:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_pr_metadata(self) -> None:
        adapter = _make_adapter()
        respx.get(f"{GITHUB_API_BASE}/repos/acme/app/pulls/42").mock(
            return_value=httpx.Response(200, json={"title": "My PR", "state": "open"})
        )
        result = await adapter.fetch_thread("github:acme/app:42")
        assert result["id"] == "github:acme/app:42"
        assert result["metadata"]["prTitle"] == "My PR"

    @pytest.mark.asyncio
    @respx.mock
    async def test_includes_review_comment_id_for_review_thread(self) -> None:
        adapter = _make_adapter()
        respx.get(f"{GITHUB_API_BASE}/repos/acme/app/pulls/42").mock(
            return_value=httpx.Response(200, json={"title": "PR", "state": "open"})
        )
        result = await adapter.fetch_thread("github:acme/app:42:rc:200")
        assert result["metadata"]["reviewCommentId"] == 200

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_issue_metadata_for_issue_thread(self) -> None:
        adapter = _make_adapter()
        respx.get(f"{GITHUB_API_BASE}/repos/acme/app/issues/10").mock(
            return_value=httpx.Response(200, json={"title": "Bug", "state": "open"})
        )
        result = await adapter.fetch_thread("github:acme/app:issue:10")
        assert result["metadata"]["type"] == "issue"
        assert result["metadata"]["issueTitle"] == "Bug"


class TestListThreads:
    @pytest.mark.asyncio
    @respx.mock
    async def test_lists_open_prs_as_threads(self) -> None:
        adapter = _make_adapter()
        respx.get(re.compile(rf"{re.escape(GITHUB_API_BASE)}/repos/acme/app/pulls.*")).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "First PR",
                        "body": "",
                        "state": "open",
                        "user": {"id": 1, "login": "a", "type": "User"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-02T00:00:00Z",
                    },
                    {
                        "number": 2,
                        "title": "Second PR",
                        "body": "body",
                        "state": "open",
                        "user": {"id": 1, "login": "a", "type": "User"},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-03T00:00:00Z",
                    },
                ],
            )
        )
        result = await adapter.list_threads("github:acme/app")
        assert len(result["threads"]) == 2
        assert result["threads"][0]["id"] == "github:acme/app:1"

    @pytest.mark.asyncio
    async def test_rejects_invalid_channel_id(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(ValidationError):
            await adapter.list_threads("slack:C123")


class TestFetchChannelInfo:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_repo_metadata(self) -> None:
        adapter = _make_adapter()
        respx.get(f"{GITHUB_API_BASE}/repos/acme/app").mock(
            return_value=httpx.Response(
                200,
                json={
                    "full_name": "acme/app",
                    "description": "Test repo",
                    "visibility": "public",
                    "default_branch": "main",
                    "open_issues_count": 7,
                },
            )
        )
        info = await adapter.fetch_channel_info("github:acme/app")
        assert info["name"] == "acme/app"
        assert info["metadata"]["defaultBranch"] == "main"

    @pytest.mark.asyncio
    async def test_rejects_invalid_channel_id(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(ValidationError):
            await adapter.fetch_channel_info("slack:C123")


# ---------------------------------------------------------------------------
# parse_message / render_formatted / thread helpers on instance
# ---------------------------------------------------------------------------


class TestParseMessage:
    def test_parses_issue_comment_raw(self) -> None:
        adapter = _make_adapter()
        raw = {
            "type": "issue_comment",
            "comment": {
                "id": 1,
                "body": "Hey",
                "user": {"id": 2, "login": "u", "type": "User"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            "repository": {
                "owner": {"id": 1, "login": "acme", "type": "User"},
                "name": "app",
            },
            "prNumber": 42,
            "threadType": "pr",
        }
        message = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert message.id == "1"
        assert message.thread_id == "github:acme/app:42"

    def test_parses_issue_comment_from_issue_thread(self) -> None:
        adapter = _make_adapter()
        raw = {
            "type": "issue_comment",
            "comment": {
                "id": 1,
                "body": "Hey",
                "user": {"id": 2, "login": "u", "type": "User"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            "repository": {
                "owner": {"id": 1, "login": "acme", "type": "User"},
                "name": "app",
            },
            "prNumber": 10,
            "threadType": "issue",
        }
        message = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert message.thread_id == "github:acme/app:issue:10"

    def test_defaults_to_pr_when_thread_type_missing(self) -> None:
        adapter = _make_adapter()
        raw = {
            "type": "issue_comment",
            "comment": {
                "id": 1,
                "body": "Hey",
                "user": {"id": 2, "login": "u", "type": "User"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            "repository": {
                "owner": {"id": 1, "login": "acme", "type": "User"},
                "name": "app",
            },
            "prNumber": 42,
        }
        message = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert message.thread_id == "github:acme/app:42"

    def test_parses_review_comment(self) -> None:
        adapter = _make_adapter()
        raw = {
            "type": "review_comment",
            "comment": {
                "id": 200,
                "body": "Line comment",
                "user": {"id": 2, "login": "rev", "type": "User"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            "repository": {
                "owner": {"id": 1, "login": "acme", "type": "User"},
                "name": "app",
            },
            "prNumber": 42,
        }
        message = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert message.thread_id == "github:acme/app:42:rc:200"

    def test_marks_edited_messages(self) -> None:
        adapter = _make_adapter()
        raw = {
            "type": "issue_comment",
            "comment": {
                "id": 1,
                "body": "Hey",
                "user": {"id": 2, "login": "u", "type": "User"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            },
            "repository": {
                "owner": {"id": 1, "login": "acme", "type": "User"},
                "name": "app",
            },
            "prNumber": 42,
        }
        message = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert message.metadata.edited is True
        assert message.metadata.edited_at is not None

    def test_detects_is_me(self) -> None:
        adapter = _make_adapter(botUserId=5)
        raw = {
            "type": "issue_comment",
            "comment": {
                "id": 1,
                "body": "Hey",
                "user": {"id": 5, "login": "bot", "type": "Bot"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            "repository": {
                "owner": {"id": 1, "login": "acme", "type": "User"},
                "name": "app",
            },
            "prNumber": 42,
        }
        message = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert message.author.is_me is True
        assert message.author.is_bot is True


class TestRenderFormatted:
    def test_renders_simple_markdown(self) -> None:
        adapter = _make_adapter()
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "Hello world"}],
                },
            ],
        }
        assert adapter.render_formatted(ast) == "Hello world"


# ---------------------------------------------------------------------------
# start_typing / channel_id_from_thread_id / encode/decode on instance
# ---------------------------------------------------------------------------


class TestMisc:
    @pytest.mark.asyncio
    async def test_start_typing_is_noop(self) -> None:
        adapter = _make_adapter()
        await adapter.start_typing("github:acme/app:42")
        await adapter.start_typing("github:acme/app:42", "thinking...")

    def test_channel_id_from_thread_id_on_instance(self) -> None:
        adapter = _make_adapter()
        assert adapter.channel_id_from_thread_id("github:acme/app:42") == "github:acme/app"
        assert adapter.channel_id_from_thread_id("github:acme/app:42:rc:200") == "github:acme/app"

    def test_encode_decode_roundtrip_on_instance(self) -> None:
        adapter = _make_adapter()
        original = {"owner": "acme", "repo": "app", "prNumber": 42, "type": "pr"}
        assert adapter.decode_thread_id(adapter.encode_thread_id(original)) == original


# ---------------------------------------------------------------------------
# create_github_adapter factory
# ---------------------------------------------------------------------------


class TestCreateGitHubAdapterFactory:
    def test_creates_with_pat_config(self) -> None:
        adapter = create_github_adapter(
            {"token": "ghp", "webhookSecret": "secret", "userName": "bot"}
        )
        assert adapter.name == "github"

    def test_falls_back_to_env_for_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "from-env")
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
        adapter = create_github_adapter()
        assert adapter.name == "github"

    def test_falls_back_to_env_for_app_credentials(
        self, monkeypatch: pytest.MonkeyPatch, rsa_private_key_pem: str
    ) -> None:
        monkeypatch.setenv("GITHUB_APP_ID", "12345")
        monkeypatch.setenv("GITHUB_PRIVATE_KEY", rsa_private_key_pem)
        monkeypatch.setenv("GITHUB_INSTALLATION_ID", "99")
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
        adapter = create_github_adapter()
        assert adapter.is_multi_tenant is False

    def test_accepts_api_url_for_github_enterprise(self) -> None:
        adapter = create_github_adapter(
            {
                "token": "ghp",
                "webhookSecret": "secret",
                "userName": "bot",
                "apiUrl": "https://ghe.example.com/api/v3",
            }
        )
        assert adapter.api_url == "https://ghe.example.com/api/v3"

    def test_resolves_api_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://ghe.env.example.com/api/v3")
        adapter = create_github_adapter(
            {"token": "ghp", "webhookSecret": "secret", "userName": "bot"}
        )
        assert adapter.api_url == "https://ghe.env.example.com/api/v3"

    def test_config_api_url_takes_precedence_over_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://env-wins.example.com/api/v3")
        adapter = create_github_adapter(
            {
                "token": "ghp",
                "webhookSecret": "secret",
                "userName": "bot",
                "apiUrl": "https://config-wins.example.com/api/v3",
            }
        )
        assert adapter.api_url == "https://config-wins.example.com/api/v3"

    def test_requires_webhook_secret(self) -> None:
        with pytest.raises(ValidationError):
            create_github_adapter({"token": "ghp", "userName": "bot"})

    def test_requires_auth(self) -> None:
        with pytest.raises(ValidationError):
            create_github_adapter({"webhookSecret": "secret", "userName": "bot"})
