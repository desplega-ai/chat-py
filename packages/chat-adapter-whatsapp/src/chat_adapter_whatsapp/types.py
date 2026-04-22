"""WhatsApp adapter types.

Python port of upstream ``packages/adapter-whatsapp/src/types.ts``. Mirrors
the Meta Graph API (WhatsApp Business Cloud API) payload shapes verbatim so
JSON parsed from webhooks / REST responses can flow through without further
munging.

See https://developers.facebook.com/docs/whatsapp/cloud-api for the canonical
definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from chat import Logger


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class WhatsAppAdapterConfig(TypedDict, total=False):
    """WhatsApp adapter configuration.

    Requires a System User access token for API calls and an App Secret for
    webhook signature verification.

    See https://developers.facebook.com/docs/whatsapp/cloud-api/get-started.
    """

    accessToken: str
    apiUrl: str
    apiVersion: str
    appSecret: str
    logger: Logger
    phoneNumberId: str
    userName: str
    verifyToken: str


# ---------------------------------------------------------------------------
# Thread ID
# ---------------------------------------------------------------------------


class WhatsAppThreadId(TypedDict):
    """Decoded thread ID for WhatsApp.

    WhatsApp conversations are always 1:1 between a business phone number and
    a user. There is no concept of threads or channels.

    Format: ``whatsapp:{phoneNumberId}:{userWaId}``.
    """

    phoneNumberId: str
    userWaId: str


# ---------------------------------------------------------------------------
# Webhook payloads
# ---------------------------------------------------------------------------


class WhatsAppContactProfile(TypedDict, total=False):
    name: str


class WhatsAppContact(TypedDict, total=False):
    """Contact information from an inbound message."""

    profile: WhatsAppContactProfile
    wa_id: str


class WhatsAppAudioPayload(TypedDict, total=False):
    id: str
    mime_type: str
    sha256: str
    voice: bool


class WhatsAppButtonPayload(TypedDict, total=False):
    payload: str
    text: str


class WhatsAppContext(TypedDict, total=False):
    from_: str
    id: str


class WhatsAppDocumentPayload(TypedDict, total=False):
    caption: str
    filename: str
    id: str
    mime_type: str
    sha256: str


class WhatsAppImagePayload(TypedDict, total=False):
    caption: str
    id: str
    mime_type: str
    sha256: str


class WhatsAppButtonReply(TypedDict, total=False):
    id: str
    title: str


class WhatsAppListReply(TypedDict, total=False):
    description: str
    id: str
    title: str


class WhatsAppInteractivePayload(TypedDict, total=False):
    button_reply: WhatsAppButtonReply
    list_reply: WhatsAppListReply
    type: Literal["button_reply", "list_reply"]


class WhatsAppLocationPayload(TypedDict, total=False):
    address: str
    latitude: float
    longitude: float
    name: str
    url: str


class WhatsAppReactionPayload(TypedDict, total=False):
    emoji: str
    message_id: str


class WhatsAppStickerPayload(TypedDict, total=False):
    animated: bool
    id: str
    mime_type: str
    sha256: str


class WhatsAppTextPayload(TypedDict, total=False):
    body: str


class WhatsAppVideoPayload(TypedDict, total=False):
    caption: str
    id: str
    mime_type: str
    sha256: str


class WhatsAppVoicePayload(TypedDict, total=False):
    id: str
    mime_type: str
    sha256: str


WhatsAppMessageType = Literal[
    "text",
    "image",
    "document",
    "audio",
    "video",
    "voice",
    "sticker",
    "location",
    "contacts",
    "interactive",
    "button",
    "reaction",
    "order",
    "system",
]


class WhatsAppInboundMessage(TypedDict, total=False):
    """Inbound message from a user.

    See https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples.
    """

    audio: WhatsAppAudioPayload
    button: WhatsAppButtonPayload
    context: WhatsAppContext
    document: WhatsAppDocumentPayload
    from_: str
    id: str
    image: WhatsAppImagePayload
    interactive: WhatsAppInteractivePayload
    location: WhatsAppLocationPayload
    reaction: WhatsAppReactionPayload
    sticker: WhatsAppStickerPayload
    text: WhatsAppTextPayload
    timestamp: str
    type: WhatsAppMessageType
    video: WhatsAppVideoPayload
    voice: WhatsAppVoicePayload


class WhatsAppMetadata(TypedDict, total=False):
    display_phone_number: str
    phone_number_id: str


class WhatsAppConversationOrigin(TypedDict, total=False):
    type: str


class WhatsAppConversation(TypedDict, total=False):
    expiration_timestamp: str
    id: str
    origin: WhatsAppConversationOrigin


class WhatsAppPricing(TypedDict, total=False):
    billable: bool
    category: str
    pricing_model: str


class WhatsAppStatus(TypedDict, total=False):
    """Message delivery / read status update."""

    conversation: WhatsAppConversation
    id: str
    pricing: WhatsAppPricing
    recipient_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    timestamp: str


class WhatsAppWebhookValue(TypedDict, total=False):
    """The value payload containing messages, contacts, and statuses."""

    contacts: list[WhatsAppContact]
    messages: list[WhatsAppInboundMessage]
    messaging_product: Literal["whatsapp"]
    metadata: WhatsAppMetadata
    statuses: list[WhatsAppStatus]


class WhatsAppWebhookChange(TypedDict, total=False):
    """A change object containing the actual event data."""

    field: Literal["messages"]
    value: WhatsAppWebhookValue


class WhatsAppWebhookEntry(TypedDict, total=False):
    """A single entry in the webhook notification."""

    changes: list[WhatsAppWebhookChange]
    id: str


class WhatsAppWebhookPayload(TypedDict, total=False):
    """Top-level webhook notification envelope from Meta.

    See https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components.
    """

    entry: list[WhatsAppWebhookEntry]
    object: Literal["whatsapp_business_account"]


# ---------------------------------------------------------------------------
# API responses
# ---------------------------------------------------------------------------


class WhatsAppMediaResponse(TypedDict, total=False):
    """Response from the media URL endpoint.

    See https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media#get-media-url.
    """

    file_size: int
    id: str
    messaging_product: Literal["whatsapp"]
    mime_type: str
    sha256: str
    url: str


class WhatsAppSendContactEntry(TypedDict, total=False):
    input: str
    wa_id: str


class WhatsAppSendMessageEntry(TypedDict, total=False):
    id: str


class WhatsAppSendResponse(TypedDict, total=False):
    """Response from sending a message via the Cloud API."""

    contacts: list[WhatsAppSendContactEntry]
    messages: list[WhatsAppSendMessageEntry]
    messaging_product: Literal["whatsapp"]


# ---------------------------------------------------------------------------
# Interactive message payloads
# ---------------------------------------------------------------------------


class WhatsAppButtonReplyAction(TypedDict, total=False):
    id: str
    title: str


class WhatsAppButton(TypedDict, total=False):
    reply: WhatsAppButtonReplyAction
    type: Literal["reply"]


class WhatsAppListRow(TypedDict, total=False):
    description: str
    id: str
    title: str


class WhatsAppListSection(TypedDict, total=False):
    rows: list[WhatsAppListRow]
    title: str


class WhatsAppInteractiveAction(TypedDict, total=False):
    button: str
    buttons: list[WhatsAppButton]
    sections: list[WhatsAppListSection]


class WhatsAppInteractiveBody(TypedDict):
    text: str


class WhatsAppInteractiveFooter(TypedDict, total=False):
    text: str


class WhatsAppInteractiveHeader(TypedDict, total=False):
    text: str
    type: Literal["text"]


class WhatsAppInteractiveMessage(TypedDict, total=False):
    """Interactive message payload for sending buttons or lists."""

    action: WhatsAppInteractiveAction
    body: WhatsAppInteractiveBody
    footer: WhatsAppInteractiveFooter
    header: WhatsAppInteractiveHeader
    type: Literal["button", "list"]


# ---------------------------------------------------------------------------
# Raw message
# ---------------------------------------------------------------------------


class WhatsAppRawMessage(TypedDict, total=False):
    """Platform-specific raw message type for WhatsApp."""

    contact: WhatsAppContact
    message: WhatsAppInboundMessage
    phoneNumberId: str


# ---------------------------------------------------------------------------
# API error envelope
# ---------------------------------------------------------------------------


class WhatsAppApiErrorBody(TypedDict, total=False):
    code: int
    error_data: dict[str, Any]
    error_subcode: int
    error_user_msg: str
    error_user_title: str
    fbtrace_id: str
    message: str
    type: str


class WhatsAppApiErrorEnvelope(TypedDict, total=False):
    """Meta Graph API error response envelope.

    See https://developers.facebook.com/docs/graph-api/guides/error-handling.
    """

    error: NotRequired[WhatsAppApiErrorBody]


__all__ = [
    "WhatsAppAdapterConfig",
    "WhatsAppApiErrorBody",
    "WhatsAppApiErrorEnvelope",
    "WhatsAppAudioPayload",
    "WhatsAppButton",
    "WhatsAppButtonPayload",
    "WhatsAppButtonReply",
    "WhatsAppButtonReplyAction",
    "WhatsAppContact",
    "WhatsAppContactProfile",
    "WhatsAppContext",
    "WhatsAppConversation",
    "WhatsAppConversationOrigin",
    "WhatsAppDocumentPayload",
    "WhatsAppImagePayload",
    "WhatsAppInboundMessage",
    "WhatsAppInteractiveAction",
    "WhatsAppInteractiveBody",
    "WhatsAppInteractiveFooter",
    "WhatsAppInteractiveHeader",
    "WhatsAppInteractiveMessage",
    "WhatsAppInteractivePayload",
    "WhatsAppListReply",
    "WhatsAppListRow",
    "WhatsAppListSection",
    "WhatsAppLocationPayload",
    "WhatsAppMediaResponse",
    "WhatsAppMessageType",
    "WhatsAppMetadata",
    "WhatsAppPricing",
    "WhatsAppRawMessage",
    "WhatsAppReactionPayload",
    "WhatsAppSendContactEntry",
    "WhatsAppSendMessageEntry",
    "WhatsAppSendResponse",
    "WhatsAppStatus",
    "WhatsAppStickerPayload",
    "WhatsAppTextPayload",
    "WhatsAppThreadId",
    "WhatsAppVideoPayload",
    "WhatsAppVoicePayload",
    "WhatsAppWebhookChange",
    "WhatsAppWebhookEntry",
    "WhatsAppWebhookPayload",
    "WhatsAppWebhookValue",
]
