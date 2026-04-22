"""Tests for :class:`DiscordAdapter` and :func:`verify_discord_signature`.

Mirrors the structure of upstream ``packages/adapter-discord/src/index.test.ts``
while using Python-native fixtures (``respx`` for httpx mocking, PyNaCl for
signature round-trips).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from chat_adapter_discord.adapter import (
    DISCORD_API_BASE,
    DISCORD_MAX_CONTENT_LENGTH,
    DiscordAdapter,
    DiscordSlashCommandContext,
    create_discord_adapter,
    parse_slash_command,
    verify_discord_signature,
)
from chat_adapter_discord.thread_id import encode_thread_id
from chat_adapter_shared import (
    AuthenticationError,
    NetworkError,
    PermissionError,
    ValidationError,
)
from nacl.signing import SigningKey

_ENV_KEYS = (
    "DISCORD_BOT_TOKEN",
    "DISCORD_PUBLIC_KEY",
    "DISCORD_APPLICATION_ID",
    "DISCORD_API_URL",
    "DISCORD_MENTION_ROLE_IDS",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
def public_key_hex(signing_key: SigningKey) -> str:
    return signing_key.verify_key.encode().hex()


def _make_adapter(public_key_hex: str, **overrides: Any) -> DiscordAdapter:
    config: dict[str, Any] = {
        "botToken": "test-token",
        "publicKey": public_key_hex,
        "applicationId": "test-app-id",
    }
    config.update(overrides)
    return DiscordAdapter(config)  # type: ignore[arg-type]


def _sign(signing_key: SigningKey, timestamp: str, body: bytes | str) -> str:
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    signed = timestamp.encode("utf-8") + body_bytes
    return signing_key.sign(signed).signature.hex()


def _headers(
    signing_key: SigningKey, body: bytes | str, *, timestamp: str = "1700000000"
) -> dict[str, str]:
    return {
        "x-signature-ed25519": _sign(signing_key, timestamp, body),
        "x-signature-timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# Factory + construction
# ---------------------------------------------------------------------------


class TestCreateDiscordAdapter:
    def test_creates_adapter_instance(self, public_key_hex: str) -> None:
        adapter = create_discord_adapter(
            {
                "botToken": "t",
                "publicKey": public_key_hex,
                "applicationId": "a",
            }
        )
        assert isinstance(adapter, DiscordAdapter)
        assert adapter.name == "discord"

    def test_defaults_user_name_to_bot(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        assert adapter.user_name == "bot"

    def test_uses_provided_user_name(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex, userName="custom")
        assert adapter.user_name == "custom"


class TestEnvVarResolution:
    def test_missing_bot_token_raises(self) -> None:
        with pytest.raises(ValidationError):
            DiscordAdapter({"publicKey": "x" * 64, "applicationId": "a"})

    def test_missing_public_key_raises(self) -> None:
        with pytest.raises(ValidationError):
            DiscordAdapter({"botToken": "t", "applicationId": "a"})

    def test_missing_application_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            DiscordAdapter({"botToken": "t", "publicKey": "x" * 64})

    def test_resolves_all_from_env(
        self, monkeypatch: pytest.MonkeyPatch, public_key_hex: str
    ) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token")
        monkeypatch.setenv("DISCORD_PUBLIC_KEY", public_key_hex)
        monkeypatch.setenv("DISCORD_APPLICATION_ID", "env-app")
        monkeypatch.setenv("DISCORD_API_URL", "https://api.example.test/")
        adapter = DiscordAdapter()
        assert adapter.bot_token == "env-token"
        assert adapter.public_key == public_key_hex
        assert adapter.application_id == "env-app"
        assert adapter.api_base_url == "https://api.example.test/"

    def test_resolves_mention_role_ids_from_env(
        self, monkeypatch: pytest.MonkeyPatch, public_key_hex: str
    ) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
        monkeypatch.setenv("DISCORD_PUBLIC_KEY", public_key_hex)
        monkeypatch.setenv("DISCORD_APPLICATION_ID", "a")
        monkeypatch.setenv("DISCORD_MENTION_ROLE_IDS", "role1,role2, role3 ")
        adapter = DiscordAdapter()
        assert adapter.mention_role_ids == ["role1", "role2", "role3"]

    def test_default_logger_when_not_provided(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        assert adapter.logger is not None

    def test_config_wins_over_env(
        self, monkeypatch: pytest.MonkeyPatch, public_key_hex: str
    ) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token")
        adapter = _make_adapter(public_key_hex, botToken="explicit")
        assert adapter.bot_token == "explicit"

    def test_api_url_env_fallback(
        self, monkeypatch: pytest.MonkeyPatch, public_key_hex: str
    ) -> None:
        monkeypatch.setenv("DISCORD_API_URL", "https://env.example/api")
        adapter = _make_adapter(public_key_hex)
        assert adapter.api_base_url == "https://env.example/api"

    def test_api_url_defaults(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        assert adapter.api_base_url == DISCORD_API_BASE


# ---------------------------------------------------------------------------
# Thread ID passthroughs
# ---------------------------------------------------------------------------


class TestThreadIdHelpers:
    def test_encode(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = adapter.encode_thread_id({"guildId": "g", "channelId": "c"})
        assert tid == "discord:g:c"

    def test_encode_with_thread(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = adapter.encode_thread_id({"guildId": "g", "channelId": "c", "threadId": "t"})
        assert tid == "discord:g:c:t"

    def test_decode(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        decoded = adapter.decode_thread_id("discord:g:c:t")
        assert decoded == {"guildId": "g", "channelId": "c", "threadId": "t"}

    def test_is_dm_true(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        assert adapter.is_dm("discord:@me:channel") is True

    def test_is_dm_false(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        assert adapter.is_dm("discord:g:c") is False


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestVerifyDiscordSignature:
    def test_rejects_missing_signature(self, public_key_hex: str) -> None:
        assert verify_discord_signature(public_key_hex, None, "123", b"body") is False

    def test_rejects_missing_timestamp(self, public_key_hex: str) -> None:
        assert verify_discord_signature(public_key_hex, "deadbeef", None, b"body") is False

    def test_rejects_invalid_signature(self, public_key_hex: str, signing_key: SigningKey) -> None:
        _ = signing_key
        assert verify_discord_signature(public_key_hex, "f" * 128, "1", b"body") is False

    def test_accepts_valid_signature(self, public_key_hex: str, signing_key: SigningKey) -> None:
        body = b'{"type": 1}'
        ts = "1700000000"
        sig = _sign(signing_key, ts, body)
        assert verify_discord_signature(public_key_hex, sig, ts, body) is True

    def test_rejects_bad_hex_key(self, signing_key: SigningKey) -> None:
        assert verify_discord_signature("zz", "ab", "1", b"body") is False


# ---------------------------------------------------------------------------
# handle_webhook
# ---------------------------------------------------------------------------


class TestHandleWebhookSignature:
    @pytest.mark.asyncio
    async def test_401_missing_signature(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        status, _, body = await adapter.handle_webhook(b'{"type": 1}', {})
        assert status == 401
        assert "signature" in body.lower()

    @pytest.mark.asyncio
    async def test_401_invalid_signature(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        status, _, _ = await adapter.handle_webhook(
            b'{"type": 1}',
            {
                "x-signature-ed25519": "f" * 128,
                "x-signature-timestamp": "1",
            },
        )
        assert status == 401


class TestHandleWebhookPing:
    @pytest.mark.asyncio
    async def test_responds_pong(self, public_key_hex: str, signing_key: SigningKey) -> None:
        adapter = _make_adapter(public_key_hex)
        body = b'{"type": 1}'
        status, headers, response_body = await adapter.handle_webhook(
            body, _headers(signing_key, body)
        )
        assert status == 200
        assert headers["content-type"] == "application/json"
        assert json.loads(response_body) == {"type": 1}


class TestHandleWebhookJsonParsing:
    @pytest.mark.asyncio
    async def test_400_invalid_json(self, public_key_hex: str, signing_key: SigningKey) -> None:
        adapter = _make_adapter(public_key_hex)
        body = b"not-json"
        status, _, _ = await adapter.handle_webhook(body, _headers(signing_key, body))
        assert status == 400

    @pytest.mark.asyncio
    async def test_400_unknown_interaction_type(
        self, public_key_hex: str, signing_key: SigningKey
    ) -> None:
        adapter = _make_adapter(public_key_hex)
        body = b'{"type": 99}'
        status, _, _ = await adapter.handle_webhook(body, _headers(signing_key, body))
        assert status == 400


class TestHandleWebhookMessageComponent:
    @pytest.mark.asyncio
    async def test_dispatches_button_click(
        self, public_key_hex: str, signing_key: SigningKey
    ) -> None:
        adapter = _make_adapter(public_key_hex)
        process_action = AsyncMock()

        class _Chat:
            pass

        chat = _Chat()
        chat.process_action = process_action  # type: ignore[attr-defined]
        await adapter.initialize(chat)
        interaction = {
            "type": 3,
            "data": {"custom_id": "click-me"},
            "guild_id": "guild1",
            "channel_id": "channel1",
            "channel": {"type": 0},
            "message": {"id": "msg123"},
            "member": {"user": {"id": "user1", "username": "alice"}},
        }
        body = json.dumps(interaction).encode("utf-8")
        status, _, response = await adapter.handle_webhook(body, _headers(signing_key, body))
        assert status == 200
        assert json.loads(response)["type"] == 6  # DEFERRED_UPDATE_MESSAGE
        process_action.assert_awaited_once()
        call_args = process_action.call_args.args[0]
        assert call_args["actionId"] == "click-me"
        assert call_args["messageId"] == "msg123"
        assert call_args["threadId"] == "discord:guild1:channel1"


class TestHandleWebhookApplicationCommand:
    @pytest.mark.asyncio
    async def test_dispatches_slash_command(
        self, public_key_hex: str, signing_key: SigningKey
    ) -> None:
        adapter = _make_adapter(public_key_hex)
        process_slash = AsyncMock()

        class _Chat:
            pass

        chat = _Chat()
        chat.process_slash_command = process_slash  # type: ignore[attr-defined]
        await adapter.initialize(chat)
        interaction = {
            "type": 2,
            "token": "interaction-token",
            "data": {
                "name": "help",
                "options": [{"name": "query", "type": 3, "value": "how to"}],
            },
            "guild_id": "guild1",
            "channel_id": "channel1",
            "channel": {"type": 0},
            "member": {"user": {"id": "user1", "username": "alice"}},
        }
        body = json.dumps(interaction).encode("utf-8")
        status, _, response = await adapter.handle_webhook(body, _headers(signing_key, body))
        assert status == 200
        assert json.loads(response)["type"] == 5  # DEFERRED_CHANNEL_MESSAGE
        process_slash.assert_awaited_once()
        event = process_slash.call_args.args[0]
        assert event["command"] == "/help"
        assert event["text"] == "how to"
        assert event["channelId"] == "discord:guild1:channel1"


class TestHandleWebhookGatewayForwarding:
    @pytest.mark.asyncio
    async def test_rejects_bad_gateway_token(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        status, _, _ = await adapter.handle_webhook(
            b'{"type": "GATEWAY_MESSAGE_CREATE"}',
            {"x-discord-gateway-token": "wrong"},
        )
        assert status == 401

    @pytest.mark.asyncio
    async def test_accepts_valid_gateway_token(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)

        class _Chat:
            handle_incoming_message = AsyncMock()

        chat = _Chat()
        await adapter.initialize(chat)
        event = {
            "type": "GATEWAY_MESSAGE_CREATE",
            "data": {
                "id": "msg1",
                "guild_id": "guild1",
                "channel_id": "channel1",
                "author": {"id": "user1", "username": "alice"},
                "content": "hello",
                "timestamp": "2026-04-22T09:00:00Z",
            },
        }
        status, _headers, response = await adapter.handle_webhook(
            json.dumps(event).encode("utf-8"),
            {"x-discord-gateway-token": "test-token"},
        )
        assert status == 200
        assert json.loads(response) == {"ok": True}
        chat.handle_incoming_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reaction_add_dispatched(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)

        class _Chat:
            process_reaction = AsyncMock()

        chat = _Chat()
        await adapter.initialize(chat)
        event = {
            "type": "GATEWAY_MESSAGE_REACTION_ADD",
            "data": {
                "guild_id": "guild1",
                "channel_id": "channel1",
                "message_id": "msg1",
                "emoji": {"name": "\U0001f525", "id": None},
                "user": {"id": "user1", "username": "alice"},
            },
        }
        status, _, _ = await adapter.handle_webhook(
            json.dumps(event).encode("utf-8"),
            {"x-discord-gateway-token": "test-token"},
        )
        assert status == 200
        chat.process_reaction.assert_awaited_once()
        payload = chat.process_reaction.call_args.args[0]
        assert payload["added"] is True
        assert payload["messageId"] == "msg1"


# ---------------------------------------------------------------------------
# parse_message
# ---------------------------------------------------------------------------


class TestParseMessage:
    def test_parses_basic_message(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "channel1",
            "guild_id": "guild1",
            "content": "Hello **world**",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "user1", "username": "alice", "global_name": "Alice"},
        }
        msg = adapter.parse_message(raw)
        assert msg.id == "msg1"
        assert msg.thread_id == "discord:guild1:channel1"
        assert msg.author.user_id == "user1"
        assert msg.author.user_name == "alice"
        assert msg.author.full_name == "Alice"
        assert msg.author.is_bot is False

    def test_parses_dm_when_guild_missing(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "channel1",
            "content": "hi",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "user1", "username": "alice"},
        }
        msg = adapter.parse_message(raw)
        assert msg.thread_id.startswith("discord:@me:")

    def test_bot_message_flagged(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "c",
            "guild_id": "g",
            "content": "beep",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "bot1", "username": "bot", "bot": True},
        }
        msg = adapter.parse_message(raw)
        assert msg.author.is_bot is True

    def test_flags_messages_from_bot_itself(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "c",
            "guild_id": "g",
            "content": "beep",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "test-app-id", "username": "me"},
        }
        msg = adapter.parse_message(raw)
        assert msg.author.is_me is True

    def test_parses_edited_message(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "c",
            "guild_id": "g",
            "content": "edited",
            "timestamp": "2026-04-22T09:00:00Z",
            "edited_timestamp": "2026-04-22T09:05:00Z",
            "author": {"id": "user1", "username": "alice"},
        }
        msg = adapter.parse_message(raw)
        assert msg.metadata.edited is True
        assert msg.metadata.edited_at is not None

    def test_thread_starter_uses_referenced_message(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "starter1",
            "channel_id": "c",
            "guild_id": "g",
            "type": 21,
            "content": "",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "starter-bot", "username": "bot"},
            "referenced_message": {
                "id": "real-msg",
                "channel_id": "c",
                "guild_id": "g",
                "content": "original content",
                "timestamp": "2026-04-22T08:00:00Z",
                "author": {"id": "user1", "username": "alice"},
            },
        }
        msg = adapter.parse_message(raw)
        assert msg.id == "real-msg"
        assert "original" in msg.text

    def test_parses_attachments(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "c",
            "guild_id": "g",
            "content": "see pic",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "u", "username": "a"},
            "attachments": [
                {
                    "url": "https://cdn.example/img.png",
                    "filename": "img.png",
                    "content_type": "image/png",
                    "size": 1024,
                    "width": 640,
                    "height": 480,
                }
            ],
        }
        msg = adapter.parse_message(raw)
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "image"
        assert msg.attachments[0].name == "img.png"

    def test_falls_back_to_username_when_global_name_missing(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        raw = {
            "id": "msg1",
            "channel_id": "c",
            "guild_id": "g",
            "content": "hi",
            "timestamp": "2026-04-22T09:00:00Z",
            "author": {"id": "u", "username": "alice"},
        }
        msg = adapter.parse_message(raw)
        assert msg.author.full_name == "alice"


# ---------------------------------------------------------------------------
# render_formatted
# ---------------------------------------------------------------------------


class TestRenderFormatted:
    def test_renders_bold_ast(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        ast: Any = {
            "type": "root",
            "children": [
                {
                    "type": "paragraph",
                    "children": [
                        {"type": "strong", "children": [{"type": "text", "value": "bold"}]}
                    ],
                }
            ],
        }
        out = adapter.render_formatted(ast)
        assert "**bold**" in out


# ---------------------------------------------------------------------------
# parse_slash_command
# ---------------------------------------------------------------------------


class TestParseSlashCommand:
    def test_no_options(self) -> None:
        assert parse_slash_command("help") == ("/help", "")

    def test_options_flattened(self) -> None:
        out = parse_slash_command("ask", [{"name": "q", "type": 3, "value": "why"}])
        assert out == ("/ask", "why")

    def test_subcommand_path(self) -> None:
        options = [{"name": "status", "type": 1, "options": [{"name": "raw", "value": "details"}]}]
        cmd, text = parse_slash_command("admin", options)
        assert cmd == "/admin status"
        assert text == "details"


# ---------------------------------------------------------------------------
# Outbound REST — post / edit / delete / reactions / typing
# ---------------------------------------------------------------------------


def _thread_id() -> str:
    return encode_thread_id({"guildId": "guild1", "channelId": "channel1"})


class TestPostMessage:
    @pytest.mark.asyncio
    async def test_happy_path(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(200, json={"id": "msg001"})
            )
            result = await adapter.post_message(tid, "hello")
        assert result["id"] == "msg001"
        assert result["threadId"] == tid
        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bot test-token"
        assert request.headers["Content-Type"] == "application/json"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["content"] == "hello"

    @pytest.mark.asyncio
    async def test_posts_to_thread_channel(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = encode_thread_id(
            {"guildId": "guild1", "channelId": "channel1", "threadId": "thread9"}
        )
        with respx.mock(assert_all_called=True) as router:
            router.post(f"{DISCORD_API_BASE}/channels/thread9/messages").mock(
                return_value=httpx.Response(200, json={"id": "msg2"})
            )
            result = await adapter.post_message(tid, "hi")
        assert result["threadId"] == tid

    @pytest.mark.asyncio
    async def test_truncates_long_content(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        payload: dict[str, Any] = {}
        with respx.mock() as router:
            route = router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(200, json={"id": "x"})
            )
            await adapter.post_message(tid, "a" * 2500)
            payload = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert len(payload["content"]) <= DISCORD_MAX_CONTENT_LENGTH
        assert payload["content"].endswith("...")

    @pytest.mark.asyncio
    async def test_short_content_untouched(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock() as router:
            route = router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(200, json={"id": "x"})
            )
            await adapter.post_message(tid, "short")
            payload = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert payload["content"] == "short"

    @pytest.mark.asyncio
    async def test_card_message_has_no_content(self, public_key_hex: str) -> None:
        from chat import Actions, Button, Card

        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        card_msg = {
            "card": Card(
                title="Test Card",
                children=[Actions([Button(id="btn1", label="Click me")])],
            )
        }
        with respx.mock() as router:
            route = router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(200, json={"id": "x"})
            )
            await adapter.post_message(tid, card_msg)
            payload = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert "content" not in payload or payload["content"] == ""
        assert payload["embeds"]
        assert payload["components"]

    @pytest.mark.asyncio
    async def test_maps_401_to_authentication_error(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock() as router:
            router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(401, json={"message": "Unauthorized"})
            )
            with pytest.raises(AuthenticationError):
                await adapter.post_message(tid, "hi")

    @pytest.mark.asyncio
    async def test_maps_403_to_permission_error(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock() as router:
            router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(403, json={"message": "Forbidden"})
            )
            with pytest.raises(PermissionError):
                await adapter.post_message(tid, "hi")

    @pytest.mark.asyncio
    async def test_maps_500_to_network_error(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock() as router:
            router.post(f"{DISCORD_API_BASE}/channels/channel1/messages").mock(
                return_value=httpx.Response(500, text="boom")
            )
            with pytest.raises(NetworkError):
                await adapter.post_message(tid, "hi")


class TestEditMessage:
    @pytest.mark.asyncio
    async def test_edits_via_patch(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.patch(f"{DISCORD_API_BASE}/channels/channel1/messages/msg001").mock(
                return_value=httpx.Response(200, json={"id": "msg001"})
            )
            result = await adapter.edit_message(tid, "msg001", "updated")
        assert result["id"] == "msg001"
        payload = json.loads(route.calls.last.request.content.decode("utf-8"))
        assert payload["content"] == "updated"

    @pytest.mark.asyncio
    async def test_card_edit_clears_content(self, public_key_hex: str) -> None:
        from chat import Card

        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock() as router:
            route = router.patch(f"{DISCORD_API_BASE}/channels/channel1/messages/msg001").mock(
                return_value=httpx.Response(200, json={"id": "msg001"})
            )
            await adapter.edit_message(tid, "msg001", {"card": Card(title="Hi")})
            payload = json.loads(route.calls.last.request.content.decode("utf-8"))
        # When replacing text with a card, content is cleared.
        assert payload.get("content", "") == ""
        assert payload["embeds"]


class TestDeleteMessage:
    @pytest.mark.asyncio
    async def test_deletes_via_delete(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            router.delete(f"{DISCORD_API_BASE}/channels/channel1/messages/msg001").mock(
                return_value=httpx.Response(204)
            )
            await adapter.delete_message(tid, "msg001")


class TestReactions:
    @pytest.mark.asyncio
    async def test_add_reaction(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.put(
                url__regex=rf"{DISCORD_API_BASE}/channels/channel1/messages/msg1/reactions/.*/@me"
            ).mock(return_value=httpx.Response(204))
            await adapter.add_reaction(tid, "msg1", "\U0001f525")
            assert route.called

    @pytest.mark.asyncio
    async def test_remove_reaction(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            route = router.delete(
                url__regex=rf"{DISCORD_API_BASE}/channels/channel1/messages/msg1/reactions/.*/@me"
            ).mock(return_value=httpx.Response(204))
            await adapter.remove_reaction(tid, "msg1", "\U0001f525")
            assert route.called


class TestStartTyping:
    @pytest.mark.asyncio
    async def test_posts_typing(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            router.post(f"{DISCORD_API_BASE}/channels/channel1/typing").mock(
                return_value=httpx.Response(204)
            )
            await adapter.start_typing(tid)


# ---------------------------------------------------------------------------
# Readers — fetch_messages / fetch_thread / fetch_channel_info / list_threads
# ---------------------------------------------------------------------------


class TestFetchMessages:
    @pytest.mark.asyncio
    async def test_fetches_and_reverses(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        messages_raw = [
            {
                "id": "m2",
                "channel_id": "channel1",
                "guild_id": "guild1",
                "content": "two",
                "timestamp": "2026-04-22T09:02:00Z",
                "author": {"id": "u", "username": "a"},
            },
            {
                "id": "m1",
                "channel_id": "channel1",
                "guild_id": "guild1",
                "content": "one",
                "timestamp": "2026-04-22T09:01:00Z",
                "author": {"id": "u", "username": "a"},
            },
        ]
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{DISCORD_API_BASE}/channels/channel1/messages?limit=50").mock(
                return_value=httpx.Response(200, json=messages_raw)
            )
            result = await adapter.fetch_messages(tid)
        assert [m.id for m in result["messages"]] == ["m1", "m2"]
        assert result["nextCursor"] is None

    @pytest.mark.asyncio
    async def test_cursor_sets_before_param(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        raw_batch = [
            {
                "id": f"m{i}",
                "channel_id": "channel1",
                "guild_id": "guild1",
                "content": f"msg-{i}",
                "timestamp": "2026-04-22T09:00:00Z",
                "author": {"id": "u", "username": "a"},
            }
            for i in range(3)
        ]
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{DISCORD_API_BASE}/channels/channel1/messages?limit=3&before=prev").mock(
                return_value=httpx.Response(200, json=raw_batch)
            )
            result = await adapter.fetch_messages(tid, {"limit": 3, "cursor": "prev"})
        assert result["nextCursor"] == "m2"


class TestFetchThread:
    @pytest.mark.asyncio
    async def test_fetches_thread(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{DISCORD_API_BASE}/channels/channel1").mock(
                return_value=httpx.Response(
                    200, json={"id": "channel1", "name": "general", "type": 0}
                )
            )
            result = await adapter.fetch_thread(tid)
        assert result["id"] == tid
        assert result["channelName"] == "general"
        assert result["isDM"] is False


class TestFetchChannelInfo:
    @pytest.mark.asyncio
    async def test_fetches_channel(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        cid = _thread_id()
        with respx.mock(assert_all_called=True) as router:
            router.get(f"{DISCORD_API_BASE}/channels/channel1").mock(
                return_value=httpx.Response(200, json={"name": "general", "type": 0})
            )
            result = await adapter.fetch_channel_info(cid)
        assert result["name"] == "general"
        assert result["isDM"] is False

    @pytest.mark.asyncio
    async def test_invalid_channel_id(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        with pytest.raises(ValidationError):
            await adapter.fetch_channel_info("discord:g:")


class TestOpenDm:
    @pytest.mark.asyncio
    async def test_opens_dm(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        with respx.mock(assert_all_called=True) as router:
            router.post(f"{DISCORD_API_BASE}/users/@me/channels").mock(
                return_value=httpx.Response(200, json={"id": "dm1"})
            )
            tid = await adapter.open_dm("user1")
        assert tid == "discord:@me:dm1"


# ---------------------------------------------------------------------------
# Slash-command deferred response via contextvar
# ---------------------------------------------------------------------------


class TestSlashContextDeferredResponse:
    @pytest.mark.asyncio
    async def test_initial_response_patches_original(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        ctx = DiscordSlashCommandContext(
            channel_id=tid, interaction_token="tok1", initial_response_sent=False
        )
        token = adapter._request_context.set(ctx)
        try:
            with respx.mock(assert_all_called=True) as router:
                router.patch(
                    f"{DISCORD_API_BASE}/webhooks/test-app-id/tok1/messages/@original"
                ).mock(return_value=httpx.Response(200, json={"id": "initial"}))
                result = await adapter.post_message(tid, "ok")
        finally:
            adapter._request_context.reset(token)
        assert result["id"] == "initial"
        assert ctx.initial_response_sent is True

    @pytest.mark.asyncio
    async def test_followup_posts_to_webhook(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        tid = _thread_id()
        ctx = DiscordSlashCommandContext(
            channel_id=tid, interaction_token="tok1", initial_response_sent=True
        )
        token = adapter._request_context.set(ctx)
        try:
            with respx.mock(assert_all_called=True) as router:
                router.post(f"{DISCORD_API_BASE}/webhooks/test-app-id/tok1?wait=true").mock(
                    return_value=httpx.Response(200, json={"id": "followup"})
                )
                result = await adapter.post_message(tid, "ok")
        finally:
            adapter._request_context.reset(token)
        assert result["id"] == "followup"


# ---------------------------------------------------------------------------
# Close cleanup
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, public_key_hex: str) -> None:
        adapter = _make_adapter(public_key_hex)
        # No client yet, should be a no-op.
        await adapter.close()
        # Now instantiate a client then close.
        await adapter._get_http_client()
        await adapter.close()
        assert adapter._http_client is None
