"""Tests for :class:`LinearAdapter` and :func:`verify_linear_signature`."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from chat_adapter_linear.adapter import (
    LINEAR_API_URL,
    LinearAdapter,
    create_linear_adapter,
    verify_linear_signature,
)
from chat_adapter_shared import AdapterError, AuthenticationError, ValidationError

WEBHOOK_SECRET = "test-secret"
_ENV_KEYS = (
    "LINEAR_API_KEY",
    "LINEAR_ACCESS_TOKEN",
    "LINEAR_CLIENT_ID",
    "LINEAR_CLIENT_SECRET",
    "LINEAR_CLIENT_CREDENTIALS_CLIENT_ID",
    "LINEAR_CLIENT_CREDENTIALS_CLIENT_SECRET",
    "LINEAR_CLIENT_CREDENTIALS_SCOPES",
    "LINEAR_WEBHOOK_SECRET",
    "LINEAR_BOT_USERNAME",
    "LINEAR_API_URL",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _sign(body: str | bytes, secret: str = WEBHOOK_SECRET) -> str:
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def _make_adapter(**overrides: Any) -> LinearAdapter:
    config: dict[str, Any] = {
        "apiKey": "test-token",
        "webhookSecret": WEBHOOK_SECRET,
        "userName": "test-bot",
    }
    config.update(overrides)
    return LinearAdapter(config)


def _make_mock_state() -> Any:
    cache: dict[str, Any] = {}
    state = MagicMock()
    state.get = AsyncMock(side_effect=lambda key: cache.get(key))
    state.set = AsyncMock(side_effect=lambda key, value: cache.__setitem__(key, value))
    state.delete = AsyncMock(side_effect=lambda key: cache.pop(key, None))
    return state


def _make_mock_chat(state: Any = None) -> Any:
    chat = MagicMock()
    chat.get_state = MagicMock(return_value=state)
    chat.process_message = AsyncMock()
    return chat


# ---------------------------------------------------------------------------
# verify_linear_signature
# ---------------------------------------------------------------------------


class TestVerifyLinearSignature:
    def test_accepts_a_valid_signature(self) -> None:
        body = b'{"hello":"world"}'
        sig = _sign(body)
        assert verify_linear_signature(WEBHOOK_SECRET, sig, body) is True

    def test_rejects_invalid_signature(self) -> None:
        body = b'{"hello":"world"}'
        assert verify_linear_signature(WEBHOOK_SECRET, "a" * 64, body) is False

    def test_rejects_none_signature(self) -> None:
        assert verify_linear_signature(WEBHOOK_SECRET, None, b"{}") is False

    def test_rejects_empty_signature(self) -> None:
        assert verify_linear_signature(WEBHOOK_SECRET, "", b"{}") is False

    def test_accepts_string_body(self) -> None:
        body = '{"hello":"world"}'
        sig = _sign(body)
        assert verify_linear_signature(WEBHOOK_SECRET, sig, body) is True

    def test_rejects_mismatched_body(self) -> None:
        sig = _sign(b"original")
        assert verify_linear_signature(WEBHOOK_SECRET, sig, b"different") is False


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_requires_webhook_secret(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            LinearAdapter({"apiKey": "token"})
        assert "webhookSecret" in str(excinfo.value)

    def test_uses_env_webhook_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "env-secret")
        adapter = LinearAdapter({"apiKey": "token"})
        assert adapter.webhook_secret == "env-secret"

    def test_requires_authentication(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            LinearAdapter({"webhookSecret": WEBHOOK_SECRET})
        assert "Authentication" in str(excinfo.value)

    def test_accepts_api_key(self) -> None:
        adapter = LinearAdapter({"apiKey": "token", "webhookSecret": WEBHOOK_SECRET})
        assert adapter.name == "linear"
        assert adapter.is_multi_tenant is False

    def test_accepts_access_token(self) -> None:
        adapter = LinearAdapter({"accessToken": "token", "webhookSecret": WEBHOOK_SECRET})
        assert adapter.is_multi_tenant is False

    def test_accepts_multi_tenant(self) -> None:
        adapter = LinearAdapter(
            {
                "clientId": "c-1",
                "clientSecret": "s-1",
                "webhookSecret": WEBHOOK_SECRET,
            }
        )
        assert adapter.is_multi_tenant is True

    def test_rejects_partial_multi_tenant(self) -> None:
        with pytest.raises(ValidationError):
            LinearAdapter({"clientId": "c-1", "webhookSecret": WEBHOOK_SECRET})

    def test_uses_env_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", WEBHOOK_SECRET)
        monkeypatch.setenv("LINEAR_API_KEY", "env-key")
        adapter = LinearAdapter()
        assert adapter.is_multi_tenant is False

    def test_default_mode_is_comments(self) -> None:
        adapter = _make_adapter()
        assert adapter.mode == "comments"

    def test_agent_sessions_mode(self) -> None:
        adapter = _make_adapter(mode="agent-sessions")
        assert adapter.mode == "agent-sessions"

    def test_uses_default_user_name(self) -> None:
        adapter = LinearAdapter({"apiKey": "token", "webhookSecret": WEBHOOK_SECRET})
        assert adapter.user_name == "linear-bot"

    def test_accepts_custom_user_name(self) -> None:
        adapter = _make_adapter(userName="my-bot")
        assert adapter.user_name == "my-bot"

    def test_accepts_client_credentials(self) -> None:
        adapter = LinearAdapter(
            {
                "clientCredentials": {"clientId": "c-1", "clientSecret": "s-1"},
                "webhookSecret": WEBHOOK_SECRET,
            }
        )
        assert adapter.is_multi_tenant is False


# ---------------------------------------------------------------------------
# Thread ID delegation
# ---------------------------------------------------------------------------


class TestThreadIdHelpers:
    def test_encode_thread_id(self) -> None:
        adapter = _make_adapter()
        assert adapter.encode_thread_id({"issueId": "abc"}) == "linear:abc"

    def test_decode_thread_id(self) -> None:
        adapter = _make_adapter()
        assert adapter.decode_thread_id("linear:abc") == {"issueId": "abc"}

    def test_channel_id_from_thread_id(self) -> None:
        adapter = _make_adapter()
        assert adapter.channel_id_from_thread_id("linear:abc:c:c-1") == "linear:abc"


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------


class TestHandleWebhook:
    @pytest.mark.asyncio
    async def test_rejects_invalid_signature(self) -> None:
        adapter = _make_adapter()
        status, _headers, body = await adapter.handle_webhook(b"{}", {"Linear-Signature": "bad"})
        assert status == 401
        assert body == "Invalid signature"

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self) -> None:
        adapter = _make_adapter()
        raw = b"not-json"
        sig = _sign(raw)
        status, _headers, _body = await adapter.handle_webhook(raw, {"Linear-Signature": sig})
        assert status == 400

    @pytest.mark.asyncio
    async def test_dispatches_comment_created(self) -> None:
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        adapter = _make_adapter()
        await adapter.initialize(chat)

        payload = {
            "type": "Comment",
            "action": "create",
            "organizationId": "org-1",
            "data": {
                "id": "comment-1",
                "issueId": "issue-1",
                "body": "Hello",
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
                "user": {
                    "id": "user-1",
                    "name": "Alice",
                    "url": "https://linear.app/acme/profiles/alice",
                },
            },
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _sign(raw)
        status, _headers, _body = await adapter.handle_webhook(raw, {"Linear-Signature": sig})
        assert status == 200
        chat.process_message.assert_awaited_once()
        args = chat.process_message.await_args.args
        assert args[0] is adapter
        assert args[1].startswith("linear:issue-1:c:")

    @pytest.mark.asyncio
    async def test_ignores_comment_in_agent_session_mode(self) -> None:
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        adapter = _make_adapter(mode="agent-sessions")
        await adapter.initialize(chat)

        payload = {
            "type": "Comment",
            "action": "create",
            "organizationId": "org-1",
            "data": {
                "id": "comment-1",
                "issueId": "issue-1",
                "body": "Hello",
                "user": {"id": "user-1", "name": "Alice", "url": None},
            },
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _sign(raw)
        status, _h, _b = await adapter.handle_webhook(raw, {"Linear-Signature": sig})
        assert status == 200
        chat.process_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_agent_session_event_in_comments_mode(self) -> None:
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        adapter = _make_adapter()
        await adapter.initialize(chat)

        payload = {
            "type": "AgentSessionEvent",
            "action": "prompted",
            "organizationId": "org-1",
            "agentSession": {"id": "session-1", "issueId": "issue-1"},
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _sign(raw)
        status, _h, _b = await adapter.handle_webhook(raw, {"Linear-Signature": sig})
        assert status == 200
        chat.process_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatches_agent_session_prompted(self) -> None:
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        adapter = _make_adapter(mode="agent-sessions")
        await adapter.initialize(chat)

        payload = {
            "type": "AgentSessionEvent",
            "action": "prompted",
            "organizationId": "org-1",
            "agentSession": {"id": "session-1", "issueId": "issue-1"},
            "agentActivity": {
                "sourceCommentId": "comment-1",
                "content": {"body": "Please help"},
                "createdAt": "2024-01-01T00:00:00Z",
                "user": {
                    "id": "user-1",
                    "name": "Alice",
                    "url": "https://linear.app/acme/profiles/alice",
                },
            },
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _sign(raw)
        status, _h, _b = await adapter.handle_webhook(raw, {"Linear-Signature": sig})
        assert status == 200
        chat.process_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oauth_revoked_deletes_installation(self) -> None:
        state = _make_mock_state()
        chat = _make_mock_chat(state)
        adapter = LinearAdapter(
            {
                "clientId": "c-1",
                "clientSecret": "s-1",
                "webhookSecret": WEBHOOK_SECRET,
            }
        )
        adapter._chat = chat
        await state.set(
            "linear:installation:org-1",
            {"organizationId": "org-1", "accessToken": "tok", "botUserId": "bot"},
        )

        payload = {
            "type": "OAuthApp",
            "action": "revoked",
            "organizationId": "org-1",
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _sign(raw)
        status, _h, _b = await adapter.handle_webhook(raw, {"Linear-Signature": sig})
        assert status == 200
        state.delete.assert_awaited()


# ---------------------------------------------------------------------------
# post_message / edit_message / delete_message
# ---------------------------------------------------------------------------


class TestPostMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_to_issue(self) -> None:
        adapter = _make_adapter()
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": {
                                "id": "new-comment",
                                "body": "Hello",
                                "createdAt": "2024-01-01T00:00:00Z",
                                "updatedAt": "2024-01-01T00:00:00Z",
                                "url": "https://linear.app/x",
                            },
                        }
                    }
                },
            )
        )
        result = await adapter.post_message("linear:issue-1", "Hello")
        assert result["id"] == "new-comment"
        assert result["threadId"] == "linear:issue-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_reply_in_comment_thread(self) -> None:
        adapter = _make_adapter()
        route = respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": {
                                "id": "reply-1",
                                "body": "Reply",
                                "createdAt": "2024-01-01T00:00:00Z",
                                "updatedAt": "2024-01-01T00:00:00Z",
                            },
                        }
                    }
                },
            )
        )
        await adapter.post_message("linear:issue-1:c:c-1", {"markdown": "Reply"})
        assert route.called
        sent = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert sent["variables"]["input"]["issueId"] == "issue-1"
        assert sent["variables"]["input"]["parentId"] == "c-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_failed_create(self) -> None:
        adapter = _make_adapter()
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"commentCreate": {"success": False, "comment": None}}},
            )
        )
        with pytest.raises(AdapterError):
            await adapter.post_message("linear:issue-1", "Hello")

    @pytest.mark.asyncio
    @respx.mock
    async def test_agent_session_posts_activity(self) -> None:
        adapter = _make_adapter(mode="agent-sessions")
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "agentActivityCreate": {
                            "success": True,
                            "agentActivity": {
                                "id": "act-1",
                                "sourceCommentId": "c-1",
                            },
                        }
                    }
                },
            )
        )
        result = await adapter.post_message("linear:issue-1:s:session-1", "Response text")
        assert result["id"] == "act-1"


class TestEditMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_edits_comment(self) -> None:
        adapter = _make_adapter()
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "commentUpdate": {
                            "success": True,
                            "comment": {
                                "id": "c-1",
                                "body": "Updated",
                                "createdAt": "2024-01-01T00:00:00Z",
                                "updatedAt": "2024-01-01T00:01:00Z",
                            },
                        }
                    }
                },
            )
        )
        result = await adapter.edit_message("linear:issue-1", "c-1", "Updated")
        assert result["id"] == "c-1"

    @pytest.mark.asyncio
    async def test_rejects_edit_on_agent_session_thread(self) -> None:
        adapter = _make_adapter(mode="agent-sessions")
        with pytest.raises(AdapterError):
            await adapter.edit_message("linear:issue-1:s:session-1", "activity-1", "text")


class TestDeleteMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_deletes_comment(self) -> None:
        adapter = _make_adapter()
        route = respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"commentDelete": {"success": True}}})
        )
        await adapter.delete_message("linear:issue-1", "c-1")
        assert route.called

    @pytest.mark.asyncio
    async def test_rejects_delete_on_agent_session_thread(self) -> None:
        adapter = _make_adapter(mode="agent-sessions")
        with pytest.raises(AdapterError):
            await adapter.delete_message("linear:issue-1:s:session-1", "activity-1")


# ---------------------------------------------------------------------------
# Reactions, typing, streaming
# ---------------------------------------------------------------------------


class TestReactions:
    @pytest.mark.asyncio
    @respx.mock
    async def test_add_reaction_with_string_emoji(self) -> None:
        adapter = _make_adapter()
        route = respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"reactionCreate": {"success": True}}})
        )
        await adapter.add_reaction("linear:issue-1:c:c-1", "c-1", "thumbsup")
        assert route.called
        sent = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert sent["variables"]["input"]["emoji"] == "thumbsup"

    @pytest.mark.asyncio
    async def test_remove_reaction_is_noop(self) -> None:
        adapter = _make_adapter()
        # Should not raise
        await adapter.remove_reaction("linear:issue-1", "c-1", "thumbsup")


class TestTyping:
    @pytest.mark.asyncio
    async def test_start_typing_is_noop_in_comment_thread(self) -> None:
        adapter = _make_adapter()
        # Should not raise or make network calls
        await adapter.start_typing("linear:issue-1:c:c-1")

    @pytest.mark.asyncio
    @respx.mock
    async def test_start_typing_in_agent_session(self) -> None:
        adapter = _make_adapter(mode="agent-sessions")
        route = respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "agentActivityCreate": {
                            "success": True,
                            "agentActivity": {"id": "a"},
                        }
                    }
                },
            )
        )
        await adapter.start_typing("linear:issue-1:s:s-1", "Working...")
        assert route.called
        sent = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert sent["variables"]["input"]["content"]["type"] == "thought"
        assert sent["variables"]["input"]["ephemeral"] is True


class TestStream:
    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_accumulates_and_posts(self) -> None:
        adapter = _make_adapter()
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "commentCreate": {
                            "success": True,
                            "comment": {
                                "id": "new",
                                "body": "Hello world",
                                "createdAt": "2024-01-01T00:00:00Z",
                                "updatedAt": "2024-01-01T00:00:00Z",
                            },
                        }
                    }
                },
            )
        )

        async def gen() -> Any:
            yield "Hello "
            yield {"type": "markdown_text", "text": "world"}

        result = await adapter.stream("linear:issue-1", gen())
        assert result["id"] == "new"


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


class TestFetchThread:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_thread_metadata(self) -> None:
        adapter = _make_adapter()
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "issue": {
                            "id": "issue-1",
                            "identifier": "ENG-42",
                            "title": "Do the thing",
                            "url": "https://linear.app/acme/issue/ENG-42",
                        }
                    }
                },
            )
        )
        thread = await adapter.fetch_thread("linear:issue-1")
        assert thread["id"] == "linear:issue-1"
        assert thread["channelId"] == "linear:issue-1"
        assert thread["channelName"] == "ENG-42: Do the thing"


class TestFetchMessages:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_issue_level_messages(self) -> None:
        adapter = _make_adapter()
        respx.post(LINEAR_API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "comments": {
                            "nodes": [
                                {
                                    "id": "c-1",
                                    "body": "Hello",
                                    "createdAt": "2024-01-01T00:00:00Z",
                                    "updatedAt": "2024-01-01T00:00:00Z",
                                    "user": {
                                        "id": "u-1",
                                        "displayName": "Alice",
                                        "name": "Alice Zhang",
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
            )
        )
        result = await adapter.fetch_messages("linear:issue-1")
        assert len(result["messages"]) == 1
        assert result["nextCursor"] is None


# ---------------------------------------------------------------------------
# parse_message / render_formatted
# ---------------------------------------------------------------------------


class TestParseMessage:
    def test_parses_standard_comment(self) -> None:
        adapter = _make_adapter()
        raw: dict[str, Any] = {
            "kind": "comment",
            "organizationId": "org-1",
            "comment": {
                "id": "c-1",
                "body": "Hello world",
                "issueId": "issue-1",
                "parentId": None,
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
                "user": {
                    "type": "user",
                    "id": "u-1",
                    "displayName": "alice",
                    "fullName": "Alice",
                },
            },
        }
        msg = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert msg.id == "c-1"
        assert msg.text == "Hello world"
        assert msg.thread_id == "linear:issue-1:c:c-1"
        assert msg.author.user_name == "alice"
        assert msg.is_mention is False

    def test_parses_agent_session_comment_as_mention(self) -> None:
        adapter = _make_adapter(mode="agent-sessions")
        raw: dict[str, Any] = {
            "kind": "agent_session_comment",
            "organizationId": "org-1",
            "agentSessionId": "session-1",
            "comment": {
                "id": "c-1",
                "body": "Please help",
                "issueId": "issue-1",
                "parentId": None,
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
                "user": {
                    "type": "user",
                    "id": "u-1",
                    "displayName": "alice",
                    "fullName": "Alice",
                },
            },
        }
        msg = adapter.parse_message(raw)  # type: ignore[arg-type]
        assert msg.is_mention is True
        assert msg.thread_id == "linear:issue-1:c:c-1:s:session-1"


class TestRenderFormatted:
    def test_renders_ast(self) -> None:
        adapter = _make_adapter()
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "hi"}],
                },
            ],
        }
        assert adapter.render_formatted(ast).strip() == "hi"


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


class TestOAuth:
    @pytest.mark.asyncio
    async def test_handle_oauth_callback_requires_multi_tenant(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(ValidationError):
            await adapter.handle_oauth_callback(
                {"code": "abc"}, {"redirectUri": "https://example.com/cb"}
            )

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_requires_redirect_uri(self) -> None:
        adapter = LinearAdapter(
            {
                "clientId": "c-1",
                "clientSecret": "s-1",
                "webhookSecret": WEBHOOK_SECRET,
            }
        )
        with pytest.raises(ValidationError):
            await adapter.handle_oauth_callback({"code": "abc"}, {})

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_requires_code(self) -> None:
        adapter = LinearAdapter(
            {
                "clientId": "c-1",
                "clientSecret": "s-1",
                "webhookSecret": WEBHOOK_SECRET,
            }
        )
        with pytest.raises(ValidationError):
            await adapter.handle_oauth_callback({}, {"redirectUri": "https://example.com/cb"})

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_rejects_error_response(self) -> None:
        adapter = LinearAdapter(
            {
                "clientId": "c-1",
                "clientSecret": "s-1",
                "webhookSecret": WEBHOOK_SECRET,
            }
        )
        with pytest.raises(AuthenticationError):
            await adapter.handle_oauth_callback(
                {"error": "access_denied", "error_description": "user declined"},
                {"redirectUri": "https://example.com/cb"},
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateLinearAdapterFactory:
    def test_factory_returns_adapter(self) -> None:
        adapter = create_linear_adapter({"apiKey": "token", "webhookSecret": WEBHOOK_SECRET})
        assert isinstance(adapter, LinearAdapter)
