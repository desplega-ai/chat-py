"""Tests for the Slack adapter façade.

Mirrors upstream ``packages/adapter-slack/test/index.test.ts`` coverage for the
parts that are ported in the Phase 2 vertical slice:

- signature verification
- thread ID codec + Slack message URL parser
- ``SlackAdapter`` constructor / props / ``get_channel_visibility``
- ``create_slack_adapter`` factory + env-var fallbacks
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from chat_adapter_shared import ValidationError
from chat_adapter_slack.adapter import (
    SlackAdapter,
    channel_id_from_thread_id,
    create_slack_adapter,
    decode_thread_id,
    encode_thread_id,
    is_dm_thread_id,
    parse_slack_message_url,
    verify_signature,
)

# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def _sign(secret: str, timestamp: str, body: str) -> str:
    base = f"v0:{timestamp}:{body}".encode()
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_passes(self) -> None:
        secret = "abcdefghijklmnop"
        body = '{"token":"foo","team_id":"T1"}'
        ts = str(int(time.time()))
        sig = _sign(secret, ts, body)
        assert verify_signature(body, ts, sig, secret) is True

    def test_bad_signature_fails(self) -> None:
        secret = "abcdefghijklmnop"
        body = '{"a":1}'
        ts = str(int(time.time()))
        assert verify_signature(body, ts, "v0=deadbeef", secret) is False

    def test_bad_secret_fails(self) -> None:
        secret = "abcdefghijklmnop"
        body = '{"a":1}'
        ts = str(int(time.time()))
        sig = _sign(secret, ts, body)
        assert verify_signature(body, ts, sig, "wrong-secret") is False

    def test_stale_timestamp_fails(self) -> None:
        secret = "abcdefghijklmnop"
        body = "{}"
        now = 1_000_000
        stale = str(now - 600)  # 10 minutes in the past
        sig = _sign(secret, stale, body)
        assert verify_signature(body, stale, sig, secret, now_seconds=now) is False

    def test_future_timestamp_fails(self) -> None:
        secret = "abcdefghijklmnop"
        body = "{}"
        now = 1_000_000
        future = str(now + 600)
        sig = _sign(secret, future, body)
        assert verify_signature(body, future, sig, secret, now_seconds=now) is False

    def test_timestamp_within_skew_passes(self) -> None:
        secret = "abcdefghijklmnop"
        body = "{}"
        now = 1_000_000
        ts = str(now - 60)  # 1 minute old — well within 5 minute window
        sig = _sign(secret, ts, body)
        assert verify_signature(body, ts, sig, secret, now_seconds=now) is True

    def test_missing_signature_fails(self) -> None:
        assert verify_signature("{}", "1", None, "secret") is False

    def test_missing_timestamp_fails(self) -> None:
        assert verify_signature("{}", None, "v0=xyz", "secret") is False

    def test_missing_secret_fails(self) -> None:
        assert verify_signature("{}", "1", "v0=xyz", None) is False

    def test_non_numeric_timestamp_fails(self) -> None:
        assert verify_signature("{}", "not-a-number", "v0=xyz", "secret") is False

    def test_custom_skew_window(self) -> None:
        secret = "abcdefghijklmnop"
        body = "{}"
        now = 1_000_000
        ts = str(now - 120)
        sig = _sign(secret, ts, body)
        # Default 5 min window — passes.
        assert verify_signature(body, ts, sig, secret, now_seconds=now) is True
        # Tighter 60s window — fails.
        assert (
            verify_signature(body, ts, sig, secret, max_skew_seconds=60, now_seconds=now) is False
        )


# ---------------------------------------------------------------------------
# Thread ID codec
# ---------------------------------------------------------------------------


class TestThreadIdCodec:
    def test_encode_with_thread_ts(self) -> None:
        assert (
            encode_thread_id({"channel": "C123", "threadTs": "1234.5678"}) == "slack:C123:1234.5678"
        )

    def test_encode_without_thread_ts(self) -> None:
        assert encode_thread_id({"channel": "C123", "threadTs": ""}) == "slack:C123:"

    def test_decode_full_thread_id(self) -> None:
        assert decode_thread_id("slack:C123:1234.5678") == {
            "channel": "C123",
            "threadTs": "1234.5678",
        }

    def test_decode_channel_only(self) -> None:
        assert decode_thread_id("slack:C123") == {"channel": "C123", "threadTs": ""}

    def test_decode_rejects_wrong_adapter_prefix(self) -> None:
        with pytest.raises(ValidationError):
            decode_thread_id("teams:C123:1234.5678")

    def test_decode_rejects_too_many_parts(self) -> None:
        with pytest.raises(ValidationError):
            decode_thread_id("slack:C123:1234.5678:extra")

    def test_decode_rejects_too_few_parts(self) -> None:
        with pytest.raises(ValidationError):
            decode_thread_id("slack")

    def test_round_trip(self) -> None:
        tid = "slack:C123:1234.5678"
        assert encode_thread_id(decode_thread_id(tid)) == tid

    def test_channel_id_from_thread_id(self) -> None:
        assert channel_id_from_thread_id("slack:C123:1234.5678") == "slack:C123"

    def test_is_dm_thread_id_true(self) -> None:
        assert is_dm_thread_id("slack:D123:1234.5678") is True

    def test_is_dm_thread_id_false(self) -> None:
        assert is_dm_thread_id("slack:C123:1234.5678") is False


# ---------------------------------------------------------------------------
# Slack message URL parser
# ---------------------------------------------------------------------------


class TestParseSlackMessageUrl:
    def test_parses_standard_message_url(self) -> None:
        result = parse_slack_message_url(
            "https://myworkspace.slack.com/archives/C123ABC/p1234567890123456"
        )
        assert result == ("C123ABC", "1234567890.123456")

    def test_parses_url_with_query_string(self) -> None:
        result = parse_slack_message_url(
            "https://myworkspace.slack.com/archives/C123ABC/p1234567890123456?thread_ts=xx"
        )
        assert result == ("C123ABC", "1234567890.123456")

    def test_returns_none_for_non_matching_url(self) -> None:
        assert parse_slack_message_url("https://example.com/foo") is None

    def test_returns_none_for_missing_p_prefix(self) -> None:
        assert (
            parse_slack_message_url(
                "https://myworkspace.slack.com/archives/C123ABC/1234567890123456"
            )
            is None
        )


# ---------------------------------------------------------------------------
# SlackAdapter constructor
# ---------------------------------------------------------------------------


class TestSlackAdapterConstructor:
    def test_default_webhook_mode_requires_signing_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        with pytest.raises(ValidationError, match="signingSecret"):
            SlackAdapter({})

    def test_picks_up_signing_secret_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        adapter = SlackAdapter({})
        assert adapter.signing_secret == "env-secret"

    def test_explicit_signing_secret_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        adapter = SlackAdapter({"signingSecret": "explicit"})
        assert adapter.signing_secret == "explicit"

    def test_socket_mode_skips_signing_secret_requirement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        adapter = SlackAdapter({"mode": "socket", "appToken": "xapp-1-AAA"})
        assert adapter.mode == "socket"
        assert adapter.is_socket_mode is True

    def test_webhook_mode_is_default(self) -> None:
        adapter = SlackAdapter({"signingSecret": "s"})
        assert adapter.mode == "webhook"
        assert adapter.is_socket_mode is False

    def test_user_name_defaults_to_bot(self) -> None:
        adapter = SlackAdapter({"signingSecret": "s"})
        assert adapter.user_name == "bot"

    def test_user_name_override(self) -> None:
        adapter = SlackAdapter({"signingSecret": "s", "userName": "assistant"})
        assert adapter.user_name == "assistant"

    def test_installation_key_prefix_default(self) -> None:
        adapter = SlackAdapter({"signingSecret": "s"})
        assert adapter.installation_key_prefix == "slack:installation"

    def test_installation_key_prefix_override(self) -> None:
        adapter = SlackAdapter({"signingSecret": "s", "installationKeyPrefix": "custom:slack"})
        assert adapter.installation_key_prefix == "custom:slack"

    def test_bot_user_id_from_config(self) -> None:
        adapter = SlackAdapter({"signingSecret": "s", "botUserId": "U_BOT"})
        assert adapter.bot_user_id == "U_BOT"

    def test_zero_config_reads_bot_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        adapter = SlackAdapter({})
        assert adapter._default_bot_token == "xoxb-env"

    def test_non_zero_config_ignores_env_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        adapter = SlackAdapter({"signingSecret": "explicit"})
        assert adapter._default_bot_token is None


# ---------------------------------------------------------------------------
# SlackAdapter.verify_signature instance wrapper
# ---------------------------------------------------------------------------


class TestSlackAdapterVerifySignature:
    def test_delegates_to_module_helper(self) -> None:
        secret = "abcdefghijklmnop"
        body = "{}"
        ts = str(int(time.time()))
        sig = _sign(secret, ts, body)
        adapter = SlackAdapter({"signingSecret": secret})
        assert adapter.verify_signature(body, ts, sig) is True

    def test_rejects_with_bad_signature(self) -> None:
        adapter = SlackAdapter({"signingSecret": "abcdefghijklmnop"})
        assert adapter.verify_signature("{}", "1", "v0=bad") is False


# ---------------------------------------------------------------------------
# SlackAdapter.get_channel_visibility
# ---------------------------------------------------------------------------


class TestGetChannelVisibility:
    def _adapter(self) -> SlackAdapter:
        return SlackAdapter({"signingSecret": "s"})

    def test_public_channel_is_workspace(self) -> None:
        assert self._adapter().get_channel_visibility("slack:C123:1.1") == "workspace"

    def test_private_channel_is_private(self) -> None:
        assert self._adapter().get_channel_visibility("slack:G123:1.1") == "private"

    def test_dm_is_private(self) -> None:
        assert self._adapter().get_channel_visibility("slack:D123:1.1") == "private"

    def test_unknown_prefix_is_unknown(self) -> None:
        assert self._adapter().get_channel_visibility("slack:X999:1.1") == "unknown"

    def test_channel_tracked_as_external(self) -> None:
        adapter = self._adapter()
        adapter._external_channels.add("C123")
        assert adapter.get_channel_visibility("slack:C123:1.1") == "external"

    def test_is_dm_method(self) -> None:
        adapter = self._adapter()
        assert adapter.is_dm("slack:D123:1.1") is True
        assert adapter.is_dm("slack:C123:1.1") is False


# ---------------------------------------------------------------------------
# create_slack_adapter factory
# ---------------------------------------------------------------------------


class TestCreateSlackAdapter:
    def test_webhook_mode_requires_signing_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        with pytest.raises(ValidationError, match="signingSecret"):
            create_slack_adapter({"mode": "webhook"})

    def test_webhook_mode_picks_up_env_signing_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        adapter = create_slack_adapter({"mode": "webhook"})
        assert adapter.signing_secret == "env-secret"

    def test_socket_mode_requires_app_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
        with pytest.raises(ValidationError, match="appToken"):
            create_slack_adapter({"mode": "socket"})

    def test_socket_mode_picks_up_env_app_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-env")
        adapter = create_slack_adapter({"mode": "socket"})
        assert adapter.app_token == "xapp-env"
        assert adapter.mode == "socket"

    def test_socket_mode_rejects_client_id(self) -> None:
        with pytest.raises(ValidationError, match="socket mode"):
            create_slack_adapter(
                {
                    "mode": "socket",
                    "appToken": "xapp-1",
                    "clientId": "abc",
                }
            )

    def test_socket_mode_rejects_client_secret(self) -> None:
        with pytest.raises(ValidationError, match="socket mode"):
            create_slack_adapter(
                {
                    "mode": "socket",
                    "appToken": "xapp-1",
                    "clientSecret": "secret",
                }
            )

    def test_zero_config_reads_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        adapter = create_slack_adapter()
        assert adapter._default_bot_token == "xoxb-env"

    def test_zero_config_reads_client_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        monkeypatch.setenv("SLACK_CLIENT_ID", "cid")
        monkeypatch.setenv("SLACK_CLIENT_SECRET", "csec")
        adapter = create_slack_adapter()
        assert adapter.client_id == "cid"
        assert adapter.client_secret == "csec"

    def test_returns_slack_adapter_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        assert isinstance(create_slack_adapter(), SlackAdapter)
