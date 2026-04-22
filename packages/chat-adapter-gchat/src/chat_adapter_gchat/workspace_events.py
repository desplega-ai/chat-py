"""Google Workspace Events API integration for receiving all messages in a space.

Python port of upstream ``packages/adapter-gchat/src/workspace-events.ts``.

By default, Google Chat sends webhooks only for @mentions. To receive every
message in a space, create a Workspace Events subscription that publishes to
a Pub/Sub topic which in turn pushes to a webhook endpoint.

Setup flow:

1. Create a Pub/Sub topic in your GCP project.
2. Create a Pub/Sub push subscription pointing to
   ``/api/webhooks/gchat/pubsub``.
3. Call :func:`create_space_subscription` to subscribe to message events for
   a space.
4. Handle Pub/Sub messages with :func:`decode_pubsub_message`.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx

_API_BASE = "https://workspaceevents.googleapis.com/v1"
_DEFAULT_SCOPES_READ = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
]
_DEFAULT_SCOPES_MESSAGES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
]
_EVENT_TYPES_MESSAGE = [
    "google.workspace.chat.message.v1.created",
    "google.workspace.chat.message.v1.updated",
    "google.workspace.chat.reaction.v1.created",
    "google.workspace.chat.reaction.v1.deleted",
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ServiceAccountCredentials:
    """Google service account credentials."""

    client_email: str
    private_key: str
    project_id: str | None = None


class CreateSpaceSubscriptionOptions(TypedDict, total=False):
    """Options accepted by :func:`create_space_subscription`."""

    spaceName: str
    pubsubTopic: str
    ttlSeconds: int


class SpaceSubscriptionResult(TypedDict):
    """Result of :func:`create_space_subscription`."""

    name: str
    expireTime: str


class PubSubMessage(TypedDict, total=False):
    data: str
    messageId: str
    publishTime: str
    attributes: dict[str, str]


class PubSubPushMessage(TypedDict, total=False):
    """The envelope Google sends to a Pub/Sub push endpoint."""

    message: PubSubMessage
    subscription: str


class WorkspaceEventNotification(TypedDict, total=False):
    """Decoded Workspace Events notification (the shape produced by
    :func:`decode_pubsub_message`).
    """

    eventTime: str
    eventType: str
    message: dict[str, Any]
    reaction: dict[str, Any]
    subscription: str
    targetResource: str


class ServiceAccountAuth(TypedDict, total=False):
    credentials: ServiceAccountCredentials
    impersonateUser: str


class AdcAuth(TypedDict, total=False):
    useApplicationDefaultCredentials: bool
    impersonateUser: str


class CustomAuth(TypedDict):
    auth: Any


WorkspaceEventsAuthOptions = ServiceAccountAuth | AdcAuth | CustomAuth


# ---------------------------------------------------------------------------
# Auth client resolver — imported lazily so tests can mock google-auth cleanly
# ---------------------------------------------------------------------------


async def _get_access_token(auth: WorkspaceEventsAuthOptions, scopes: list[str]) -> str:
    """Return an OAuth 2.0 access token for the configured auth provider.

    Supported inputs:

    - ``{"credentials": ServiceAccountCredentials, "impersonateUser"?: str}``
    - ``{"useApplicationDefaultCredentials": True, "impersonateUser"?: str}``
    - ``{"auth": <object-with-token-or-get_access_token>}`` — pre-built client
    """

    # Custom auth first — most flexible branch for tests and advanced usage.
    if "auth" in auth and auth.get("auth") is not None:
        custom = auth["auth"]
        # If it already provides a string token, use it.
        token = getattr(custom, "token", None)
        if isinstance(token, str) and token:
            return token
        getter = getattr(custom, "get_access_token", None)
        if callable(getter):
            result = getter()
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, str):
                return result
            if hasattr(result, "access_token"):
                return str(result.access_token)
        raise ValueError("Custom auth object must expose `token` or `get_access_token`")

    if auth.get("useApplicationDefaultCredentials"):
        from google.auth import default as google_auth_default
        from google.auth.transport.requests import Request

        credentials, _ = google_auth_default(scopes=scopes)
        credentials.refresh(Request())
        return str(credentials.token)

    if "credentials" in auth:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds = auth["credentials"]
        sa_info = {
            "client_email": creds.client_email,
            "private_key": creds.private_key,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        credentials = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
        impersonate = auth.get("impersonateUser")
        if impersonate:
            credentials = credentials.with_subject(impersonate)
        credentials.refresh(Request())
        return str(credentials.token)

    raise ValueError("Invalid auth options")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_space_subscription(
    options: CreateSpaceSubscriptionOptions,
    auth: WorkspaceEventsAuthOptions,
) -> SpaceSubscriptionResult:
    """Create a Workspace Events subscription for a Chat space.

    The create call returns a long-running operation. If the operation has
    already completed, the subscription ``name`` and ``expireTime`` are
    returned; otherwise the operation name is returned and the subscription
    finishes asynchronously.
    """

    space_name = options["spaceName"]
    pubsub_topic = options["pubsubTopic"]
    ttl_seconds = options.get("ttlSeconds", 86400)

    token = await _get_access_token(auth, _DEFAULT_SCOPES_MESSAGES)

    body = {
        "targetResource": f"//chat.googleapis.com/{space_name}",
        "eventTypes": _EVENT_TYPES_MESSAGE,
        "notificationEndpoint": {"pubsubTopic": pubsub_topic},
        "payloadOptions": {"includeResource": True},
        "ttl": f"{ttl_seconds}s",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_API_BASE}/subscriptions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        operation = resp.json()

    if operation.get("done") and operation.get("response"):
        subscription = operation["response"]
        return {
            "name": subscription.get("name", ""),
            "expireTime": subscription.get("expireTime", ""),
        }

    # Operation still pending — return the operation name.
    return {"name": operation.get("name", "pending"), "expireTime": ""}


async def list_space_subscriptions(
    space_name: str,
    auth: WorkspaceEventsAuthOptions,
) -> list[dict[str, Any]]:
    """List active Workspace Events subscriptions for *space_name*.

    Each item is ``{"name": str, "expireTime": str, "eventTypes": list[str]}``.
    """

    token = await _get_access_token(auth, _DEFAULT_SCOPES_READ)
    params = {"filter": f'target_resource="//chat.googleapis.com/{space_name}"'}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_API_BASE}/subscriptions",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    subs = data.get("subscriptions") or []
    return [
        {
            "name": sub.get("name", ""),
            "expireTime": sub.get("expireTime", ""),
            "eventTypes": sub.get("eventTypes", []),
        }
        for sub in subs
    ]


async def delete_space_subscription(
    subscription_name: str,
    auth: WorkspaceEventsAuthOptions,
) -> None:
    """Delete a Workspace Events subscription by resource name."""

    token = await _get_access_token(auth, _DEFAULT_SCOPES_READ)

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{_API_BASE}/{subscription_name}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()


def decode_pubsub_message(push_message: PubSubPushMessage) -> WorkspaceEventNotification:
    """Decode a Pub/Sub push envelope into a Workspace Event notification.

    The payload uses CloudEvents wire format — event metadata lives in
    ``attributes`` (``ce-type``, ``ce-subject``, ``ce-time``) and the main
    payload is base64-encoded JSON in ``message.data``.
    """

    message = push_message.get("message", {}) or {}
    encoded = message.get("data", "")
    attributes = message.get("attributes", {}) or {}

    if encoded:
        decoded = base64.b64decode(encoded).decode("utf-8")
        payload = json.loads(decoded) if decoded else {}
    else:
        payload = {}

    notification: WorkspaceEventNotification = {
        "subscription": push_message.get("subscription", ""),
        "targetResource": attributes.get("ce-subject", ""),
        "eventType": attributes.get("ce-type", ""),
        "eventTime": attributes.get("ce-time") or message.get("publishTime", ""),
    }
    if "message" in payload:
        notification["message"] = payload["message"]
    if "reaction" in payload:
        notification["reaction"] = payload["reaction"]
    return notification


__all__ = [
    "AdcAuth",
    "CreateSpaceSubscriptionOptions",
    "CustomAuth",
    "PubSubMessage",
    "PubSubPushMessage",
    "ServiceAccountAuth",
    "ServiceAccountCredentials",
    "SpaceSubscriptionResult",
    "WorkspaceEventNotification",
    "WorkspaceEventsAuthOptions",
    "create_space_subscription",
    "decode_pubsub_message",
    "delete_space_subscription",
    "list_space_subscriptions",
]
