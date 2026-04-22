"""Tests for :class:`TeamsAdapter` and :func:`verify_bearer_token`.

Mirrors the shape of upstream ``packages/adapter-teams/src/index.test.ts``
while sticking to what this pragmatic port actually implements (no live Teams
SDK dispatch, no Graph reader).
"""

from __future__ import annotations

import os
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
import respx
from chat_adapter_shared import (
    AdapterRateLimitError,
    AuthenticationError,
    NetworkError,
    PermissionError,
    ValidationError,
)
from chat_adapter_teams.adapter import (
    BOT_FRAMEWORK_ISSUER,
    BOT_FRAMEWORK_JWKS_URL,
    DEFAULT_TEAMS_API_URL,
    TeamsAdapter,
    TeamsAuthCertificate,
    TeamsAuthFederated,
    create_teams_adapter,
    verify_bearer_token,
)
from chat_adapter_teams.thread_id import encode_thread_id

_ENV_KEYS = ("TEAMS_APP_ID", "TEAMS_APP_PASSWORD", "TEAMS_APP_TENANT_ID", "TEAMS_API_URL")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _make_adapter(**overrides: Any) -> TeamsAdapter:
    config: dict[str, Any] = {"appId": "bot-app", "appPassword": "secret"}
    config.update(overrides)
    adapter = TeamsAdapter(config)  # type: ignore[arg-type]
    adapter._get_bot_token = AsyncMock(return_value="FAKE_TOKEN")  # type: ignore[method-assign]
    return adapter


# ---------------------------------------------------------------------------
# Construction / config
# ---------------------------------------------------------------------------


class TestTeamsAdapterConstruction:
    def test_name_is_teams(self) -> None:
        assert _make_adapter().name == "teams"

    def test_certificate_auth_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TeamsAdapter(
                {
                    "appId": "x",
                    "certificate": TeamsAuthCertificate(certificate_private_key="k"),
                }
            )

    def test_reads_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEAMS_APP_ID", "env-app")
        monkeypatch.setenv("TEAMS_APP_PASSWORD", "env-pass")
        monkeypatch.setenv("TEAMS_APP_TENANT_ID", "tenant-123")
        monkeypatch.setenv("TEAMS_API_URL", "https://api.example.test/")

        adapter = TeamsAdapter()
        assert adapter.app_id == "env-app"
        assert adapter.app_password == "env-pass"
        assert adapter.app_tenant_id == "tenant-123"
        assert adapter.api_url == "https://api.example.test/"

    def test_explicit_config_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEAMS_APP_ID", "env-app")
        adapter = TeamsAdapter({"appId": "explicit"})
        assert adapter.app_id == "explicit"

    def test_defaults(self) -> None:
        adapter = TeamsAdapter()
        assert adapter.api_url == DEFAULT_TEAMS_API_URL
        assert adapter.user_name == "bot"
        assert adapter.app_type == "MultiTenant"
        assert adapter.dialog_open_timeout_ms == 5000

    def test_federated_config_stored(self) -> None:
        fed = TeamsAuthFederated(client_id="mi-client")
        adapter = TeamsAdapter({"appId": "a", "federated": fed})
        assert adapter.federated is fed
        assert fed.client_audience == "api://AzureADTokenExchange"

    def test_create_teams_adapter_factory(self) -> None:
        assert isinstance(create_teams_adapter({"appId": "a"}), TeamsAdapter)


# ---------------------------------------------------------------------------
# Thread-id passthroughs
# ---------------------------------------------------------------------------


class TestThreadIdHelpers:
    def test_encode_decode_round_trip(self) -> None:
        adapter = _make_adapter()
        tid = adapter.encode_thread_id(
            {"conversationId": "19:abc", "serviceUrl": "https://x.test/"}
        )
        assert tid.startswith("teams:")
        assert adapter.decode_thread_id(tid) == {
            "conversationId": "19:abc",
            "serviceUrl": "https://x.test/",
        }

    def test_is_dm_false_for_group(self) -> None:
        adapter = _make_adapter()
        tid = adapter.encode_thread_id(
            {"conversationId": "19:abc", "serviceUrl": "https://x.test/"}
        )
        assert adapter.is_dm(tid) is False

    def test_is_dm_true_for_personal(self) -> None:
        adapter = _make_adapter()
        tid = adapter.encode_thread_id(
            {"conversationId": "a:personal", "serviceUrl": "https://x.test/"}
        )
        assert adapter.is_dm(tid) is True

    def test_channel_id_strips_messageid_suffix(self) -> None:
        adapter = _make_adapter()
        tid = adapter.encode_thread_id(
            {
                "conversationId": "19:conv;messageid=42",
                "serviceUrl": "https://x.test/",
            }
        )
        base = adapter.channel_id_from_thread_id(tid)
        decoded = adapter.decode_thread_id(base)
        assert decoded["conversationId"] == "19:conv"
        assert decoded["serviceUrl"] == "https://x.test/"


# ---------------------------------------------------------------------------
# parse_message
# ---------------------------------------------------------------------------


class TestParseMessage:
    def test_parses_basic_activity(self) -> None:
        adapter = _make_adapter()
        activity = {
            "id": "msg-1",
            "text": "Hello **world**",
            "timestamp": "2026-04-22T09:00:00Z",
            "conversation": {"id": "19:conv"},
            "serviceUrl": "https://x.test/",
            "from": {"id": "user-1", "name": "Alice"},
        }
        message = adapter.parse_message(activity)
        assert message.id == "msg-1"
        assert message.author.user_name == "Alice"
        assert message.author.user_id == "user-1"
        assert message.author.is_me is False
        assert "Hello" in message.text
        assert message.thread_id == encode_thread_id(
            {"conversationId": "19:conv", "serviceUrl": "https://x.test/"}
        )

    def test_flags_messages_from_bot_itself(self) -> None:
        adapter = _make_adapter(appId="bot-123")
        activity = {
            "id": "m",
            "text": "hi",
            "timestamp": "2026-04-22T09:00:00Z",
            "conversation": {"id": "19:c"},
            "serviceUrl": "https://x.test/",
            "from": {"id": "bot-123", "name": "MyBot"},
        }
        message = adapter.parse_message(activity)
        assert message.author.is_me is True

    def test_flags_messages_via_suffix_match(self) -> None:
        adapter = _make_adapter(appId="bot-abc")
        activity = {
            "id": "m",
            "text": "hi",
            "timestamp": "2026-04-22T09:00:00Z",
            "conversation": {"id": "19:c"},
            "serviceUrl": "https://x.test/",
            "from": {"id": "28:bot-abc", "name": "MyBot"},
        }
        message = adapter.parse_message(activity)
        assert message.author.is_me is True

    def test_falls_back_to_now_on_bad_timestamp(self) -> None:
        adapter = _make_adapter()
        activity = {
            "id": "m",
            "text": "hi",
            "timestamp": "not-a-date",
            "conversation": {"id": "19:c"},
            "serviceUrl": "https://x.test/",
            "from": {"id": "u", "name": "U"},
        }
        message = adapter.parse_message(activity)
        assert message.metadata.date_sent is not None


# ---------------------------------------------------------------------------
# NotImplementedError stubs
# ---------------------------------------------------------------------------


class TestNotImplementedStubs:
    @pytest.mark.asyncio
    async def test_add_reaction_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.add_reaction("t", "m", "👍")

    @pytest.mark.asyncio
    async def test_remove_reaction_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.remove_reaction("t", "m", "👍")

    @pytest.mark.asyncio
    async def test_fetch_messages_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.fetch_messages("t")

    @pytest.mark.asyncio
    async def test_fetch_thread_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.fetch_thread("t")

    @pytest.mark.asyncio
    async def test_fetch_channel_messages_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.fetch_channel_messages("c")

    @pytest.mark.asyncio
    async def test_list_threads_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.list_threads("c")

    @pytest.mark.asyncio
    async def test_fetch_channel_info_raises(self) -> None:
        adapter = _make_adapter()
        from chat.errors import NotImplementedError as ChatNotImplementedError

        with pytest.raises(ChatNotImplementedError):
            await adapter.fetch_channel_info("c")


# ---------------------------------------------------------------------------
# verify_bearer_token
# ---------------------------------------------------------------------------


class _FakeJWKSClient:
    """Injectable stand-in for :class:`jwt.PyJWKClient`."""

    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        class _Result:
            def __init__(self, key: Any) -> None:
                self.key = key

        return _Result(self._key)


def _sign_token(
    payload: dict[str, Any],
    *,
    key: Any,
    alg: str = "RS256",
) -> str:
    return jwt.encode(payload, key, algorithm=alg)


@pytest.fixture(scope="module")
def _rsa_keypair() -> tuple[Any, Any]:
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


class TestVerifyBearerToken:
    def test_rejects_missing_header(self) -> None:
        assert verify_bearer_token(None, "aud") is False

    def test_rejects_empty_string_header(self) -> None:
        assert verify_bearer_token("", "aud") is False

    def test_rejects_non_bearer_scheme(self) -> None:
        assert verify_bearer_token("Basic abc", "aud") is False

    def test_rejects_empty_bearer(self) -> None:
        assert verify_bearer_token("Bearer ", "aud") is False

    def test_rejects_garbage_token(self, _rsa_keypair: tuple[Any, Any]) -> None:
        _, public = _rsa_keypair
        client = _FakeJWKSClient(public)
        assert verify_bearer_token("Bearer not.a.token", "aud", jwks_client=client) is False

    def test_accepts_valid_token(self, _rsa_keypair: tuple[Any, Any]) -> None:
        from datetime import datetime, timedelta

        private, public = _rsa_keypair
        now = datetime.now(UTC)
        token = _sign_token(
            {
                "iss": BOT_FRAMEWORK_ISSUER,
                "aud": "bot-app",
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iat": int(now.timestamp()),
            },
            key=private,
        )
        assert (
            verify_bearer_token(
                f"Bearer {token}",
                "bot-app",
                jwks_client=_FakeJWKSClient(public),
            )
            is True
        )

    def test_rejects_wrong_audience(self, _rsa_keypair: tuple[Any, Any]) -> None:
        from datetime import datetime, timedelta

        private, public = _rsa_keypair
        now = datetime.now(UTC)
        token = _sign_token(
            {
                "iss": BOT_FRAMEWORK_ISSUER,
                "aud": "someone-else",
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iat": int(now.timestamp()),
            },
            key=private,
        )
        assert (
            verify_bearer_token(
                f"Bearer {token}",
                "bot-app",
                jwks_client=_FakeJWKSClient(public),
            )
            is False
        )

    def test_rejects_wrong_issuer(self, _rsa_keypair: tuple[Any, Any]) -> None:
        from datetime import datetime, timedelta

        private, public = _rsa_keypair
        now = datetime.now(UTC)
        token = _sign_token(
            {
                "iss": "https://evil.example",
                "aud": "bot-app",
                "exp": int((now + timedelta(minutes=5)).timestamp()),
                "iat": int(now.timestamp()),
            },
            key=private,
        )
        assert (
            verify_bearer_token(
                f"Bearer {token}",
                "bot-app",
                jwks_client=_FakeJWKSClient(public),
            )
            is False
        )

    def test_rejects_expired_token(self, _rsa_keypair: tuple[Any, Any]) -> None:
        from datetime import datetime, timedelta

        private, public = _rsa_keypair
        past = datetime.now(UTC) - timedelta(hours=1)
        token = _sign_token(
            {
                "iss": BOT_FRAMEWORK_ISSUER,
                "aud": "bot-app",
                "exp": int(past.timestamp()),
                "iat": int((past - timedelta(minutes=1)).timestamp()),
            },
            key=private,
        )
        assert (
            verify_bearer_token(
                f"Bearer {token}",
                "bot-app",
                jwks_client=_FakeJWKSClient(public),
            )
            is False
        )


# ---------------------------------------------------------------------------
# handle_webhook
# ---------------------------------------------------------------------------


class TestHandleWebhook:
    @pytest.mark.asyncio
    async def test_200_when_no_app_id_configured(self) -> None:
        adapter = TeamsAdapter({})
        status, _, body = await adapter.handle_webhook({})
        assert status == 200
        assert body == "{}"

    @pytest.mark.asyncio
    async def test_200_when_no_auth_header(self) -> None:
        adapter = _make_adapter()
        status, _, _ = await adapter.handle_webhook({}, {})
        assert status == 200

    @pytest.mark.asyncio
    async def test_401_when_token_invalid(self) -> None:
        adapter = _make_adapter()
        status, _, body = await adapter.handle_webhook({}, {"Authorization": "Bearer not-a-jwt"})
        assert status == 401
        assert "unauthorized" in body.lower()

    @pytest.mark.asyncio
    async def test_reads_lowercase_authorization_header(self) -> None:
        adapter = _make_adapter()
        status, _, _ = await adapter.handle_webhook({}, {"authorization": "Bearer nope"})
        assert status == 401


# ---------------------------------------------------------------------------
# Outbound REST — happy paths and error mapping
# ---------------------------------------------------------------------------


def _thread_id() -> str:
    return encode_thread_id({"conversationId": "19:conv", "serviceUrl": "https://x.test/"})


class TestPostMessage:
    @pytest.mark.asyncio
    async def test_post_message_happy_path(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(return_value=httpx.Response(200, json={"id": "msg-1"}))

            result = await adapter.post_message(tid, {"markdown": "hello"})

        assert result["id"] == "msg-1"
        assert result["threadId"] == tid
        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer FAKE_TOKEN"
        assert request.headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_post_message_maps_401_to_authentication_error(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock() as router:
            router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(return_value=httpx.Response(401, json={"error": {"message": "Unauthorized"}}))
            with pytest.raises(AuthenticationError):
                await adapter.post_message(tid, {"markdown": "hi"})

    @pytest.mark.asyncio
    async def test_post_message_maps_403_to_permission_error(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock() as router:
            router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(return_value=httpx.Response(403, json={"error": {"message": "Forbidden"}}))
            with pytest.raises(PermissionError):
                await adapter.post_message(tid, {"markdown": "hi"})

    @pytest.mark.asyncio
    async def test_post_message_maps_429_with_retry_after(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock() as router:
            router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(
                return_value=httpx.Response(
                    429,
                    headers={"Retry-After": "42"},
                    json={"error": {"message": "slow down"}},
                )
            )
            with pytest.raises(AdapterRateLimitError) as excinfo:
                await adapter.post_message(tid, {"markdown": "hi"})
        assert excinfo.value.retry_after == 42

    @pytest.mark.asyncio
    async def test_post_message_maps_500_to_network_error(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock() as router:
            router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(return_value=httpx.Response(500, json={"error": {"message": "boom"}}))
            with pytest.raises(NetworkError):
                await adapter.post_message(tid, {"markdown": "hi"})


class TestEditAndDelete:
    @pytest.mark.asyncio
    async def test_edit_message_uses_put_with_message_id(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.put("https://x.test/v3/conversations/19:conv/activities/msg-1").mock(
                return_value=httpx.Response(200, json={})
            )

            result = await adapter.edit_message(tid, "msg-1", {"markdown": "edited"})

        assert route.called
        assert result["id"] == "msg-1"
        assert result["threadId"] == tid

    @pytest.mark.asyncio
    async def test_delete_message_uses_delete(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            router.delete("https://x.test/v3/conversations/19:conv/activities/msg-1").mock(
                return_value=httpx.Response(200, json={})
            )

            await adapter.delete_message(tid, "msg-1")


class TestStartTyping:
    @pytest.mark.asyncio
    async def test_emits_typing_activity(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(return_value=httpx.Response(200, json={"id": "x"}))

            await adapter.start_typing(tid)

        import json

        body = json.loads(route.calls.last.request.content)
        assert body == {"type": "typing"}


class TestStream:
    @pytest.mark.asyncio
    async def test_stream_posts_then_edits(self) -> None:
        adapter = _make_adapter()
        tid = _thread_id()

        async def chunks() -> Any:
            yield "Hello"
            yield " world"

        with respx.mock(assert_all_called=True) as router:
            post_route = router.post(
                "https://smba.trafficmanager.net/amer/v3/conversations/19:conv/activities"
            ).mock(return_value=httpx.Response(200, json={"id": "msg-1"}))
            put_route = router.put("https://x.test/v3/conversations/19:conv/activities/msg-1").mock(
                return_value=httpx.Response(200, json={})
            )

            result = await adapter.stream(tid, chunks())

        assert post_route.called
        assert put_route.called
        assert result["id"] == "msg-1"
        assert result["threadId"] == tid


class TestOpenDm:
    @pytest.mark.asyncio
    async def test_raises_without_tenant_id(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(ValidationError):
            await adapter.open_dm("user-1")

    @pytest.mark.asyncio
    async def test_creates_conversation_and_returns_thread_id(self) -> None:
        adapter = _make_adapter(appTenantId="tenant-1")
        with respx.mock(assert_all_called=True) as router:
            router.post("https://smba.trafficmanager.net/amer/v3/conversations").mock(
                return_value=httpx.Response(200, json={"id": "a:personal"})
            )

            tid = await adapter.open_dm("user-1")

        decoded = adapter.decode_thread_id(tid)
        assert decoded["conversationId"] == "a:personal"
        assert decoded["serviceUrl"] == DEFAULT_TEAMS_API_URL

    @pytest.mark.asyncio
    async def test_raises_when_id_missing(self) -> None:
        adapter = _make_adapter(appTenantId="tenant-1")
        with respx.mock() as router:
            router.post("https://smba.trafficmanager.net/amer/v3/conversations").mock(
                return_value=httpx.Response(200, json={})
            )
            with pytest.raises(NetworkError):
                await adapter.open_dm("user-1")


# ---------------------------------------------------------------------------
# Formatting passthrough + close
# ---------------------------------------------------------------------------


class TestFormattingAndClose:
    def test_render_formatted_delegates_to_converter(self) -> None:
        adapter = _make_adapter()
        ast = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "value": "hi"}],
                }
            ],
        }
        assert "hi" in adapter.render_formatted(ast)

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        adapter = _make_adapter()
        await adapter.close()
        await adapter.close()


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------


class TestPublicExports:
    def test_constants_exposed(self) -> None:
        assert BOT_FRAMEWORK_JWKS_URL.startswith("https://login.botframework.com")
        assert BOT_FRAMEWORK_ISSUER == "https://api.botframework.com"
        assert DEFAULT_TEAMS_API_URL.startswith("https://smba.trafficmanager.net/")

    def test_env_unchanged_after_construction(self) -> None:
        # Construction must not mutate the environment.
        before = {k: os.environ.get(k) for k in _ENV_KEYS}
        TeamsAdapter({"appId": "a"})
        after = {k: os.environ.get(k) for k in _ENV_KEYS}
        assert before == after
