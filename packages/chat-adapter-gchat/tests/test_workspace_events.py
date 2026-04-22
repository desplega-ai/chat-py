"""Tests for the Google Workspace Events helpers.

Mirrors upstream ``packages/adapter-gchat/src/workspace-events.test.ts``.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx
from chat_adapter_gchat.workspace_events import (
    PubSubPushMessage,
    create_space_subscription,
    decode_pubsub_message,
    delete_space_subscription,
    list_space_subscriptions,
)


@dataclass
class _StubAuth:
    """Custom auth stub — satisfies `_get_access_token` via ``token``."""

    token: str = "test-token"


def _make_pubsub_message(
    payload: dict[str, Any], attributes: dict[str, str] | None = None
) -> PubSubPushMessage:
    data = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    msg: dict[str, Any] = {
        "data": data,
        "messageId": "msg-123",
        "publishTime": "2024-01-15T10:00:00Z",
    }
    if attributes is not None:
        msg["attributes"] = attributes
    return {
        "message": msg,
        "subscription": "projects/my-project/subscriptions/my-sub",
    }


class TestDecodePubSubMessage:
    def test_decodes_base64_payload(self) -> None:
        push = _make_pubsub_message(
            {"message": {"text": "Hello world", "name": "spaces/ABC/messages/123"}}
        )
        result = decode_pubsub_message(push)
        assert result["message"]["text"] == "Hello world"
        assert result["subscription"] == "projects/my-project/subscriptions/my-sub"

    def test_extracts_cloudevents_attributes(self) -> None:
        push = _make_pubsub_message(
            {"message": {"text": "test"}},
            {
                "ce-type": "google.workspace.chat.message.v1.created",
                "ce-subject": "//chat.googleapis.com/spaces/ABC",
                "ce-time": "2024-01-15T10:00:00Z",
            },
        )
        result = decode_pubsub_message(push)
        assert result["eventType"] == "google.workspace.chat.message.v1.created"
        assert result["targetResource"] == "//chat.googleapis.com/spaces/ABC"
        assert result["eventTime"] == "2024-01-15T10:00:00Z"

    def test_falls_back_to_publish_time_when_attributes_missing(self) -> None:
        push = _make_pubsub_message({"message": {"text": "test"}})
        result = decode_pubsub_message(push)
        assert result["eventType"] == ""
        assert result["targetResource"] == ""
        assert result["eventTime"] == "2024-01-15T10:00:00Z"

    def test_decodes_reaction_payload(self) -> None:
        push = _make_pubsub_message(
            {
                "reaction": {
                    "name": "spaces/ABC/messages/123/reactions/456",
                    "emoji": {"unicode": "\U0001f44d"},
                }
            },
            {"ce-type": "google.workspace.chat.reaction.v1.created"},
        )
        result = decode_pubsub_message(push)
        assert result["reaction"]["name"] == "spaces/ABC/messages/123/reactions/456"
        assert result["reaction"]["emoji"]["unicode"] == "\U0001f44d"


class TestCreateSpaceSubscription:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_name_and_expire_time_when_done(self) -> None:
        route = respx.post("https://workspaceevents.googleapis.com/v1/subscriptions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "done": True,
                    "response": {
                        "name": "subscriptions/sub-abc123",
                        "expireTime": "2024-01-16T10:00:00Z",
                    },
                },
            )
        )

        result = await create_space_subscription(
            {
                "spaceName": "spaces/AAABBBCCC",
                "pubsubTopic": "projects/my-project/topics/chat-events",
            },
            {"auth": _StubAuth()},
        )

        assert route.called
        assert result["name"] == "subscriptions/sub-abc123"
        assert result["expireTime"] == "2024-01-16T10:00:00Z"

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_pending_name_when_not_done(self) -> None:
        respx.post("https://workspaceevents.googleapis.com/v1/subscriptions").mock(
            return_value=httpx.Response(200, json={"done": False, "name": "operations/op-xyz"})
        )

        result = await create_space_subscription(
            {
                "spaceName": "spaces/AAABBBCCC",
                "pubsubTopic": "projects/my-project/topics/chat-events",
            },
            {"auth": _StubAuth()},
        )
        assert result["name"] == "operations/op-xyz"
        assert result["expireTime"] == ""

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_expected_request_body(self) -> None:
        captured: dict[str, Any] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content.decode("utf-8"))
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"done": False, "name": "op-1"})

        respx.post("https://workspaceevents.googleapis.com/v1/subscriptions").mock(
            side_effect=_handler
        )

        await create_space_subscription(
            {
                "spaceName": "spaces/XYZ",
                "pubsubTopic": "projects/p/topics/t",
                "ttlSeconds": 1200,
            },
            {"auth": _StubAuth()},
        )

        assert captured["body"]["targetResource"] == "//chat.googleapis.com/spaces/XYZ"
        assert captured["body"]["notificationEndpoint"] == {"pubsubTopic": "projects/p/topics/t"}
        assert captured["body"]["ttl"] == "1200s"
        assert "google.workspace.chat.message.v1.created" in captured["body"]["eventTypes"]
        assert captured["body"]["payloadOptions"] == {"includeResource": True}
        assert captured["auth"] == "Bearer test-token"


class TestListSpaceSubscriptions:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_mapped_subscriptions(self) -> None:
        respx.get("https://workspaceevents.googleapis.com/v1/subscriptions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "subscriptions": [
                        {
                            "name": "subscriptions/sub-1",
                            "expireTime": "2024-01-16T10:00:00Z",
                            "eventTypes": [
                                "google.workspace.chat.message.v1.created",
                                "google.workspace.chat.message.v1.updated",
                            ],
                        },
                        {
                            "name": "subscriptions/sub-2",
                            "expireTime": "2024-01-17T10:00:00Z",
                            "eventTypes": ["google.workspace.chat.reaction.v1.created"],
                        },
                    ]
                },
            )
        )

        result = await list_space_subscriptions("spaces/AAABBBCCC", {"auth": _StubAuth()})
        assert len(result) == 2
        assert result[0]["name"] == "subscriptions/sub-1"
        assert result[0]["expireTime"] == "2024-01-16T10:00:00Z"
        assert result[0]["eventTypes"] == [
            "google.workspace.chat.message.v1.created",
            "google.workspace.chat.message.v1.updated",
        ]
        assert result[1]["name"] == "subscriptions/sub-2"

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_empty_when_no_subscriptions(self) -> None:
        respx.get("https://workspaceevents.googleapis.com/v1/subscriptions").mock(
            return_value=httpx.Response(200, json={})
        )

        result = await list_space_subscriptions("spaces/AAABBBCCC", {"auth": _StubAuth()})
        assert result == []


class TestDeleteSpaceSubscription:
    @pytest.mark.asyncio
    @respx.mock
    async def test_deletes_by_subscription_name(self) -> None:
        route = respx.delete(
            "https://workspaceevents.googleapis.com/v1/subscriptions/sub-abc123"
        ).mock(return_value=httpx.Response(200, json={}))

        await delete_space_subscription("subscriptions/sub-abc123", {"auth": _StubAuth()})
        assert route.called
