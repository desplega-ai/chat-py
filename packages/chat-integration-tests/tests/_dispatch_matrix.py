"""Helpers for the Phase 10 cross-adapter ``Chat.handle_webhook`` matrix.

One factory per adapter builds a :class:`chat.Chat` instance wired to a
real adapter with a :class:`chat.mock_adapter.create_mock_state` backend.
Each factory registers every handler type we might need to observe
(``on_new_mention``, ``on_direct_message``, ``on_slash_command``) via the
shared :class:`HandlerFired` event bus so individual parametrised tests
can assert which specific handler was invoked.

Signature-bearing adapters (Slack / GitHub / WhatsApp / Linear) expose
``make_*_headers(body: bytes)`` helpers that compute the real signature
against a known secret. Ed25519 (Discord) and JWT (Teams / GChat)
verifiers are monkeypatched at bot build time — real keypair signing
would require additional dev dependencies without exercising the
``Chat.handle_webhook`` dispatch surface that this matrix is pinning.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from chat import Chat
from chat.mock_adapter import create_mock_state

FIXTURE_DIR = Path(__file__).parent / "__fixtures__"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a fixture JSON, stripping the ``_provenance`` meta-field."""

    data = json.loads((FIXTURE_DIR / name).read_text())
    data.pop("_provenance", None)
    return data


# ---------------------------------------------------------------------------
# Fixture bodies
# ---------------------------------------------------------------------------

SLACK_APP_MENTION_BODY: dict[str, Any] = _load_fixture("slack_app_mention.json")
GCHAT_MESSAGE_BODY: dict[str, Any] = _load_fixture("gchat_message.json")
DISCORD_INTERACTION_BODY: dict[str, Any] = _load_fixture("discord_interaction.json")
GITHUB_ISSUE_COMMENT_BODY: dict[str, Any] = _load_fixture("github_issue_comment.json")
WHATSAPP_MESSAGE_BODY: dict[str, Any] = _load_fixture("whatsapp_message.json")
TEAMS_MESSAGE_BODY: dict[str, Any] = _load_fixture("teams_message.json")
LINEAR_COMMENT_BODY: dict[str, Any] = _load_fixture("linear_comment.json")
TELEGRAM_MESSAGE_BODY: dict[str, Any] = _load_fixture("telegram_direct_message.json")


# ---------------------------------------------------------------------------
# Shared secrets — used for dynamic signature generation across the matrix.
# ---------------------------------------------------------------------------

SLACK_SIGNING_SECRET = "matrix-slack-signing-secret-DES-196"
SLACK_BOT_USER_ID = "U_BOT"

DISCORD_BOT_TOKEN = "discord-bot-token"
DISCORD_PUBLIC_KEY_HEX = "00" * 32
DISCORD_APPLICATION_ID = "123456789012345678"

GITHUB_WEBHOOK_SECRET = "matrix-github-secret-DES-196"
GITHUB_BOT_USERNAME = "chat-py-matrix-bot"
GITHUB_TOKEN = "ghp_matrixtoken"

LINEAR_WEBHOOK_SECRET = "matrix-linear-secret-DES-196"
LINEAR_BOT_USERNAME = "chat-py-matrix-bot"
LINEAR_API_KEY = "lin_api_matrix"

WHATSAPP_APP_SECRET = "matrix-whatsapp-secret-DES-196"
WHATSAPP_ACCESS_TOKEN = "matrix-access-token"
WHATSAPP_PHONE_NUMBER_ID = "1234567890"
WHATSAPP_VERIFY_TOKEN = "matrix-verify-token"
WHATSAPP_BOT_USERNAME = "whatsapp-matrix-bot"

TELEGRAM_BOT_TOKEN = "matrix-telegram-token"
TELEGRAM_WEBHOOK_SECRET_TOKEN = "matrix-telegram-webhook-secret"
TELEGRAM_BOT_USERNAME = "chat_py_matrix_bot"

TEAMS_APP_ID = "teams-app-id-matrix"
TEAMS_APP_PASSWORD = "teams-matrix-secret"


# ---------------------------------------------------------------------------
# Header builders — signature-bearing adapters compute HMAC dynamically so
# the fixtures stay stable even if the signing convention changes.
# ---------------------------------------------------------------------------


def _slack_sign(body: bytes, ts: str) -> str:
    base = f"v0:{ts}:{body.decode()}".encode()
    return "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()


def make_slack_headers(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": _slack_sign(body, ts),
        "content-type": "application/json",
    }


def make_gchat_headers(_body: bytes) -> dict[str, str]:
    return {"authorization": "Bearer dummy-jwt", "content-type": "application/json"}


def make_discord_headers(_body: bytes) -> dict[str, str]:
    # Real signature would require ed25519; we monkeypatch the verifier in
    # the adapter factory below.
    return {
        "x-signature-ed25519": "00" * 64,
        "x-signature-timestamp": "1700000000",
        "content-type": "application/json",
    }


def make_github_headers(body: bytes) -> dict[str, str]:
    digest = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "x-hub-signature-256": f"sha256={digest}",
        "x-github-event": "issue_comment",
        "x-github-delivery": "matrix-delivery-id",
        "content-type": "application/json",
    }


def make_whatsapp_headers(body: bytes) -> dict[str, str]:
    digest = hmac.new(WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "x-hub-signature-256": f"sha256={digest}",
        "content-type": "application/json",
    }


def make_teams_headers(_body: bytes) -> dict[str, str]:
    # verify_bearer_token is monkeypatched in the factory; any bearer suffices.
    return {"authorization": "Bearer matrix-jwt", "content-type": "application/json"}


def make_linear_headers(body: bytes) -> dict[str, str]:
    # Linear uses raw HMAC-SHA256 hex (no ``sha256=`` prefix).
    digest = hmac.new(LINEAR_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "linear-signature": digest,
        "content-type": "application/json",
    }


def make_telegram_headers(_body: bytes) -> dict[str, str]:
    return {
        "x-telegram-bot-api-secret-token": TELEGRAM_WEBHOOK_SECRET_TOKEN,
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


@dataclass
class HandlerLog:
    """Captures which handlers fired during a webhook dispatch."""

    fired: list[str] = field(default_factory=list)

    def record(self, name: str) -> None:
        self.fired.append(name)

    def was_fired(self, name: str) -> bool:
        return name in self.fired


# ---------------------------------------------------------------------------
# Bot factory — one branch per adapter.
# ---------------------------------------------------------------------------


def build_bot_for(
    adapter_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Chat, HandlerLog]:
    """Instantiate a :class:`Chat` with the requested real adapter.

    Registers ``on_new_mention`` / ``on_direct_message`` / ``on_slash_command``
    handlers so the per-adapter row in the matrix can assert which one
    fired. Signature / JWT verifiers are monkeypatched where real crypto
    keypairs would otherwise be required.
    """

    log = HandlerLog()

    if adapter_name == "slack":
        monkeypatch.setenv("SLACK_SIGNING_SECRET", SLACK_SIGNING_SECRET)
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-matrix")
        from chat_adapter_slack import create_slack_adapter

        adp = create_slack_adapter()
        adp._bot_user_id = SLACK_BOT_USER_ID  # type: ignore[attr-defined]
        bot = Chat(user_name="matrix-bot", adapters={adapter_name: adp}, state=create_mock_state())

    elif adapter_name == "gchat":
        monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
        from chat_adapter_gchat import create_google_chat_adapter

        adp = create_google_chat_adapter()
        adp.bot_user_id = "users/bot"

        async def _verify_true(_: str | None) -> bool:
            return True

        adp.verify_webhook_bearer = _verify_true  # type: ignore[method-assign]
        adp.verify_pubsub_bearer = _verify_true  # type: ignore[method-assign]
        bot = Chat(user_name="matrix-bot", adapters={adapter_name: adp}, state=create_mock_state())

    elif adapter_name == "discord":
        monkeypatch.setenv("DISCORD_BOT_TOKEN", DISCORD_BOT_TOKEN)
        monkeypatch.setenv("DISCORD_PUBLIC_KEY", DISCORD_PUBLIC_KEY_HEX)
        monkeypatch.setenv("DISCORD_APPLICATION_ID", DISCORD_APPLICATION_ID)
        monkeypatch.setattr(
            "chat_adapter_discord.adapter.verify_discord_signature",
            lambda *_args, **_kwargs: True,
        )
        from chat_adapter_discord import create_discord_adapter

        adp = create_discord_adapter()
        bot = Chat(user_name="matrix-bot", adapters={adapter_name: adp}, state=create_mock_state())

    elif adapter_name == "github":
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", GITHUB_WEBHOOK_SECRET)
        monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
        monkeypatch.setenv("GITHUB_BOT_USERNAME", GITHUB_BOT_USERNAME)
        from chat_adapter_github import create_github_adapter

        adp = create_github_adapter()
        bot = Chat(
            user_name=GITHUB_BOT_USERNAME,
            adapters={adapter_name: adp},
            state=create_mock_state(),
        )

    elif adapter_name == "whatsapp":
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", WHATSAPP_ACCESS_TOKEN)
        monkeypatch.setenv("WHATSAPP_APP_SECRET", WHATSAPP_APP_SECRET)
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", WHATSAPP_PHONE_NUMBER_ID)
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", WHATSAPP_VERIFY_TOKEN)
        monkeypatch.setenv("WHATSAPP_BOT_USERNAME", WHATSAPP_BOT_USERNAME)
        from chat_adapter_whatsapp import create_whatsapp_adapter

        adp = create_whatsapp_adapter()
        bot = Chat(
            user_name=WHATSAPP_BOT_USERNAME,
            adapters={adapter_name: adp},
            state=create_mock_state(),
        )

    elif adapter_name == "teams":
        monkeypatch.setenv("TEAMS_APP_ID", TEAMS_APP_ID)
        monkeypatch.setenv("TEAMS_APP_PASSWORD", TEAMS_APP_PASSWORD)
        from chat_adapter_teams import adapter as teams_adapter_module
        from chat_adapter_teams import create_teams_adapter

        monkeypatch.setattr(teams_adapter_module, "verify_bearer_token", lambda *_a, **_kw: True)
        adp = create_teams_adapter()
        bot = Chat(user_name="matrix-bot", adapters={adapter_name: adp}, state=create_mock_state())

    elif adapter_name == "linear":
        monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", LINEAR_WEBHOOK_SECRET)
        monkeypatch.setenv("LINEAR_API_KEY", LINEAR_API_KEY)
        monkeypatch.setenv("LINEAR_BOT_USERNAME", LINEAR_BOT_USERNAME)
        from chat_adapter_linear import create_linear_adapter

        adp = create_linear_adapter()
        # Skip the outbound viewer-identity GraphQL probe.
        adp._default_bot_user_id = "matrix-bot-user-id"  # type: ignore[attr-defined]
        adp._default_organization_id = "org-abc"  # type: ignore[attr-defined]

        async def _fake_identity(_token: str) -> dict[str, str]:
            return {
                "botUserId": "matrix-bot-user-id",
                "organizationId": "org-abc",
                "displayName": LINEAR_BOT_USERNAME,
            }

        adp._fetch_viewer_identity = _fake_identity  # type: ignore[method-assign]
        bot = Chat(
            user_name=LINEAR_BOT_USERNAME,
            adapters={adapter_name: adp},
            state=create_mock_state(),
        )

    elif adapter_name == "telegram":
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", TELEGRAM_WEBHOOK_SECRET_TOKEN)
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", TELEGRAM_BOT_USERNAME)
        from chat_adapter_telegram import create_telegram_adapter

        adp = create_telegram_adapter()
        adp._bot_user_id = "9999"  # type: ignore[attr-defined]
        bot = Chat(
            user_name=TELEGRAM_BOT_USERNAME,
            adapters={adapter_name: adp},
            state=create_mock_state(),
        )
        # Bind chat directly — skips the outbound ``getMe`` probe.
        adp._chat = bot  # type: ignore[attr-defined]

    else:
        raise ValueError(f"Unknown adapter: {adapter_name}")

    # Register all three handler types — the per-row expected_handler tells
    # us which one ought to fire for the given payload.
    async def _mention(_thread: Any, _message: Any, _ctx: Any = None) -> None:
        log.record("on_new_mention")

    async def _direct(_thread: Any, _message: Any, _channel: Any = None, _ctx: Any = None) -> None:
        log.record("on_direct_message")

    async def _slash(_event: Any) -> None:
        log.record("on_slash_command")

    bot.on_new_mention(_mention)
    bot.on_direct_message(_direct)
    # Register slash command for both slack-style ``/echo`` and Discord's
    # style; the Discord adapter normalises to ``/<name>``.
    bot.on_slash_command("/echo", _slash)

    return bot, log


__all__ = [
    "DISCORD_APPLICATION_ID",
    "DISCORD_INTERACTION_BODY",
    "DISCORD_PUBLIC_KEY_HEX",
    "GCHAT_MESSAGE_BODY",
    "GITHUB_BOT_USERNAME",
    "GITHUB_ISSUE_COMMENT_BODY",
    "GITHUB_WEBHOOK_SECRET",
    "LINEAR_BOT_USERNAME",
    "LINEAR_COMMENT_BODY",
    "LINEAR_WEBHOOK_SECRET",
    "SLACK_APP_MENTION_BODY",
    "SLACK_SIGNING_SECRET",
    "TEAMS_APP_ID",
    "TEAMS_MESSAGE_BODY",
    "TELEGRAM_BOT_USERNAME",
    "TELEGRAM_MESSAGE_BODY",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_MESSAGE_BODY",
    "WHATSAPP_PHONE_NUMBER_ID",
    "HandlerLog",
    "build_bot_for",
    "make_discord_headers",
    "make_gchat_headers",
    "make_github_headers",
    "make_linear_headers",
    "make_slack_headers",
    "make_teams_headers",
    "make_telegram_headers",
    "make_whatsapp_headers",
]
