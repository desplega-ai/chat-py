"""Tests for the Google Chat adapter façade.

Mirrors upstream ``packages/adapter-gchat/src/index.test.ts`` coverage for
the parts ported in the Phase 2 vertical slice:

- Thread ID codec + channel helper
- Config resolution (service account / ADC / custom / auto)
- Env var fallbacks + mutually-exclusive auth validation
- Bearer token verification wiring (direct + Pub/Sub)
- ``parse_message`` on a realistic webhook event
- ``verify_bearer_token`` helper
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from chat_adapter_gchat.adapter import (
    GoogleChatAdapter,
    channel_id_from_thread_id,
    create_google_chat_adapter,
    verify_bearer_token,
)
from chat_adapter_gchat.workspace_events import ServiceAccountCredentials
from chat_adapter_shared import ValidationError

# ---------------------------------------------------------------------------
# Auth config resolution
# ---------------------------------------------------------------------------


class TestGoogleChatAdapterConstructor:
    def test_no_auth_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_CHAT_CREDENTIALS", raising=False)
        monkeypatch.delenv("GOOGLE_CHAT_USE_ADC", raising=False)
        with pytest.raises(ValidationError, match="Authentication is required"):
            GoogleChatAdapter({})

    def test_service_account_credentials(self) -> None:
        creds = ServiceAccountCredentials(
            client_email="bot@project.iam.gserviceaccount.com",
            private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
            project_id="proj",
        )
        adapter = GoogleChatAdapter({"credentials": creds})
        assert adapter.credentials is creds
        assert adapter.use_adc is False
        assert adapter.custom_auth is None

    def test_application_default_credentials(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        assert adapter.use_adc is True
        assert adapter.credentials is None

    def test_custom_auth(self) -> None:
        sentinel = object()
        adapter = GoogleChatAdapter({"auth": sentinel})
        assert adapter.custom_auth is sentinel

    def test_credentials_and_adc_are_mutually_exclusive(self) -> None:
        creds = ServiceAccountCredentials(client_email="a", private_key="b")
        with pytest.raises(ValidationError, match="Only one of"):
            GoogleChatAdapter(
                {
                    "credentials": creds,
                    "useApplicationDefaultCredentials": True,
                }
            )

    def test_credentials_and_auth_are_mutually_exclusive(self) -> None:
        creds = ServiceAccountCredentials(client_email="a", private_key="b")
        with pytest.raises(ValidationError, match="Only one of"):
            GoogleChatAdapter({"credentials": creds, "auth": object()})

    def test_auto_detects_credentials_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "GOOGLE_CHAT_CREDENTIALS",
            json.dumps(
                {
                    "client_email": "svc@project.iam.gserviceaccount.com",
                    "private_key": "PK",
                    "project_id": "project",
                }
            ),
        )
        adapter = GoogleChatAdapter({})
        assert adapter.credentials is not None
        assert adapter.credentials.client_email == "svc@project.iam.gserviceaccount.com"
        assert adapter.credentials.project_id == "project"
        assert adapter.use_adc is False

    def test_malformed_env_credentials_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CHAT_CREDENTIALS", "not-json")
        with pytest.raises(ValidationError, match="not valid JSON"):
            GoogleChatAdapter({})

    def test_auto_detects_adc_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_CHAT_CREDENTIALS", raising=False)
        monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
        adapter = GoogleChatAdapter({})
        assert adapter.use_adc is True
        assert adapter.credentials is None

    def test_user_name_defaults_to_bot(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        assert adapter.user_name == "bot"

    def test_user_name_override(self) -> None:
        adapter = GoogleChatAdapter(
            {"useApplicationDefaultCredentials": True, "userName": "chatsdk"}
        )
        assert adapter.user_name == "chatsdk"

    def test_env_var_wiring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CHAT_PUBSUB_TOPIC", "projects/p/topics/t")
        monkeypatch.setenv("GOOGLE_CHAT_IMPERSONATE_USER", "admin@corp.com")
        monkeypatch.setenv("GOOGLE_CHAT_PROJECT_NUMBER", "123456")
        monkeypatch.setenv("GOOGLE_CHAT_PUBSUB_AUDIENCE", "https://webhook")
        monkeypatch.setenv("GOOGLE_CHAT_API_URL", "https://api.example.com")
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        assert adapter.pubsub_topic == "projects/p/topics/t"
        assert adapter.impersonate_user == "admin@corp.com"
        assert adapter.google_chat_project_number == "123456"
        assert adapter.pubsub_audience == "https://webhook"
        assert adapter.api_url == "https://api.example.com"

    def test_config_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CHAT_PUBSUB_TOPIC", "env-topic")
        adapter = GoogleChatAdapter(
            {
                "useApplicationDefaultCredentials": True,
                "pubsubTopic": "config-topic",
            }
        )
        assert adapter.pubsub_topic == "config-topic"


# ---------------------------------------------------------------------------
# Thread ID helpers
# ---------------------------------------------------------------------------


class TestThreadIdHelpers:
    def test_channel_id_from_thread_id(self) -> None:
        assert channel_id_from_thread_id("gchat:spaces/ABC:dGVzdA") == "gchat:spaces/ABC"

    def test_adapter_wrappers_delegate(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        tid = adapter.encode_thread_id(
            {"spaceName": "spaces/XYZ", "threadName": "spaces/XYZ/threads/t1"}
        )
        assert tid.startswith("gchat:spaces/XYZ:")
        decoded = adapter.decode_thread_id(tid)
        assert decoded["spaceName"] == "spaces/XYZ"
        assert decoded["threadName"] == "spaces/XYZ/threads/t1"

    def test_channel_id_from_thread_id_method(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        tid = adapter.encode_thread_id(
            {"spaceName": "spaces/XYZ", "threadName": "spaces/XYZ/threads/t1"}
        )
        assert adapter.channel_id_from_thread_id(tid) == "gchat:spaces/XYZ"

    def test_is_dm_detects_dm_marker(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        tid = adapter.encode_thread_id({"spaceName": "spaces/DM", "isDM": True})
        assert adapter.is_dm(tid) is True

    def test_is_dm_false_for_regular_space(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        tid = adapter.encode_thread_id({"spaceName": "spaces/ABC"})
        assert adapter.is_dm(tid) is False


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------


class TestVerifyBearerToken:
    @pytest.mark.asyncio
    async def test_missing_header_fails(self) -> None:
        assert await verify_bearer_token(None, "audience") is False

    @pytest.mark.asyncio
    async def test_non_bearer_header_fails(self) -> None:
        assert await verify_bearer_token("Basic xxx", "audience") is False

    @pytest.mark.asyncio
    async def test_empty_bearer_fails(self) -> None:
        assert await verify_bearer_token("Bearer ", "audience") is False

    @pytest.mark.asyncio
    async def test_google_verify_failure_returns_false(self) -> None:
        with patch(
            "google.oauth2.id_token.verify_token",
            side_effect=ValueError("bad sig"),
        ):
            assert await verify_bearer_token("Bearer FAKE", "audience") is False

    @pytest.mark.asyncio
    async def test_valid_payload_returns_true(self) -> None:
        payload = {
            "iss": "accounts.google.com",
            "aud": "audience",
            "email": "pubsub@project.iam.gserviceaccount.com",
        }
        with patch("google.oauth2.id_token.verify_token", return_value=payload):
            assert await verify_bearer_token("Bearer FAKE", "audience") is True

    @pytest.mark.asyncio
    async def test_chat_system_issuer_accepted(self) -> None:
        payload = {
            "iss": "chat@system.gserviceaccount.com",
            "aud": "project-number",
        }
        with patch("google.oauth2.id_token.verify_token", return_value=payload):
            assert await verify_bearer_token("Bearer FAKE", "project-number") is True

    @pytest.mark.asyncio
    async def test_rejects_unknown_issuer(self) -> None:
        payload = {"iss": "https://evil.example.com", "aud": "x"}
        with patch("google.oauth2.id_token.verify_token", return_value=payload):
            assert await verify_bearer_token("Bearer FAKE", "x") is False


class TestAdapterVerifyWrappers:
    @pytest.mark.asyncio
    async def test_webhook_verify_without_project_number_passes(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        assert await adapter.verify_webhook_bearer("Bearer anything") is True
        # Warned-once flag — second call should not log again.
        assert adapter._warned_no_webhook_verification is True

    @pytest.mark.asyncio
    async def test_webhook_verify_with_project_number_delegates(self) -> None:
        adapter = GoogleChatAdapter(
            {
                "useApplicationDefaultCredentials": True,
                "googleChatProjectNumber": "123456",
            }
        )
        with patch(
            "chat_adapter_gchat.adapter.verify_bearer_token",
            return_value=True,
        ) as verify:
            assert await adapter.verify_webhook_bearer("Bearer FAKE") is True
        verify.assert_awaited_once_with("Bearer FAKE", "123456")

    @pytest.mark.asyncio
    async def test_pubsub_verify_without_audience_passes(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        assert await adapter.verify_pubsub_bearer("Bearer anything") is True
        assert adapter._warned_no_pubsub_verification is True

    @pytest.mark.asyncio
    async def test_pubsub_verify_with_audience_delegates(self) -> None:
        adapter = GoogleChatAdapter(
            {
                "useApplicationDefaultCredentials": True,
                "pubsubAudience": "https://webhook.example.com",
            }
        )
        with patch(
            "chat_adapter_gchat.adapter.verify_bearer_token",
            return_value=True,
        ) as verify:
            assert await adapter.verify_pubsub_bearer("Bearer FAKE") is True
        verify.assert_awaited_once_with("Bearer FAKE", "https://webhook.example.com")


# ---------------------------------------------------------------------------
# parse_message
# ---------------------------------------------------------------------------


def _sample_message_event() -> dict[str, Any]:
    return {
        "chat": {
            "messagePayload": {
                "space": {"name": "spaces/AAABBB", "type": "ROOM"},
                "message": {
                    "name": "spaces/AAABBB/messages/m1",
                    "sender": {
                        "name": "users/123",
                        "displayName": "Alice",
                        "type": "HUMAN",
                    },
                    "createTime": "2024-01-15T10:30:00Z",
                    "text": "Hello bot",
                    "thread": {"name": "spaces/AAABBB/threads/t1"},
                    "annotations": [
                        {
                            "type": "USER_MENTION",
                            "startIndex": 0,
                            "length": 4,
                            "userMention": {
                                "user": {
                                    "name": "users/bot",
                                    "displayName": "Bot",
                                    "type": "BOT",
                                },
                                "type": "MENTION",
                            },
                        }
                    ],
                },
            }
        }
    }


class TestParseMessage:
    def _adapter(self) -> GoogleChatAdapter:
        return GoogleChatAdapter({"useApplicationDefaultCredentials": True})

    def test_rejects_non_message_event(self) -> None:
        with pytest.raises(ValidationError, match="non-message"):
            self._adapter().parse_message({"chat": {}})

    def test_builds_message_with_thread_id(self) -> None:
        msg = self._adapter().parse_message(_sample_message_event())
        assert msg.id == "spaces/AAABBB/messages/m1"
        assert msg.text == "Hello bot"
        assert msg.author.user_id == "users/123"
        assert msg.author.user_name == "Alice"
        assert msg.author.is_bot is False
        assert msg.is_mention is True
        assert msg.thread_id.startswith("gchat:spaces/AAABBB:")

    def test_dates_are_timezone_aware(self) -> None:
        msg = self._adapter().parse_message(_sample_message_event())
        assert msg.metadata.date_sent.tzinfo is not None

    def test_bot_sender_flagged_as_bot(self) -> None:
        event = _sample_message_event()
        event["chat"]["messagePayload"]["message"]["sender"]["type"] = "BOT"
        msg = self._adapter().parse_message(event)
        assert msg.author.is_bot is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateGoogleChatAdapter:
    def test_returns_adapter_instance(self) -> None:
        adapter = create_google_chat_adapter({"useApplicationDefaultCredentials": True})
        assert isinstance(adapter, GoogleChatAdapter)

    def test_picks_up_env_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CHAT_USE_ADC", "true")
        adapter = create_google_chat_adapter()
        assert adapter.use_adc is True


# ---------------------------------------------------------------------------
# get_auth_options
# ---------------------------------------------------------------------------


class TestGetAuthOptions:
    def test_credentials_branch(self) -> None:
        creds = ServiceAccountCredentials(client_email="a", private_key="b")
        adapter = GoogleChatAdapter({"credentials": creds, "impersonateUser": "u@corp.com"})
        opts = adapter.get_auth_options()
        assert opts is not None
        assert opts["credentials"] is creds  # type: ignore[typeddict-item]
        assert opts["impersonateUser"] == "u@corp.com"  # type: ignore[typeddict-item]

    def test_adc_branch(self) -> None:
        adapter = GoogleChatAdapter({"useApplicationDefaultCredentials": True})
        opts = adapter.get_auth_options()
        assert opts == {"useApplicationDefaultCredentials": True}

    def test_custom_auth_branch(self) -> None:
        sentinel = object()
        adapter = GoogleChatAdapter({"auth": sentinel})
        opts = adapter.get_auth_options()
        assert opts is not None
        assert opts["auth"] is sentinel  # type: ignore[typeddict-item]
