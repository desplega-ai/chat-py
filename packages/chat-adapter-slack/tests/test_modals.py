"""Tests for the Slack modal converter.

Mirrors upstream ``packages/adapter-slack/src/modals.test.ts``.
"""

from __future__ import annotations

import json

from chat_adapter_slack.modals import (
    decode_modal_metadata,
    encode_modal_metadata,
    modal_to_slack_view,
)

from ._builders import (
    ExternalSelect,
    Modal,
    RadioSelect,
    Select,
    SelectOption,
    TextInput,
)

# ---------------------------------------------------------------------------
# modalToSlackView
# ---------------------------------------------------------------------------


class TestModalToSlackView:
    def test_simple_modal_with_text_input(self) -> None:
        modal = Modal(
            callback_id="feedback_form",
            title="Send Feedback",
            children=[TextInput(id="message", label="Your Feedback")],
        )
        view = modal_to_slack_view(modal)
        assert view["type"] == "modal"
        assert view["callback_id"] == "feedback_form"
        assert view["title"] == {"type": "plain_text", "text": "Send Feedback"}
        assert view["submit"] == {"type": "plain_text", "text": "Submit"}
        assert view["close"] == {"type": "plain_text", "text": "Cancel"}
        assert len(view["blocks"]) == 1
        block = view["blocks"][0]
        assert block["type"] == "input"
        assert block["block_id"] == "message"
        assert block["optional"] is False
        assert block["label"] == {"type": "plain_text", "text": "Your Feedback"}
        assert block["element"]["type"] == "plain_text_input"
        assert block["element"]["action_id"] == "message"
        assert block["element"]["multiline"] is False

    def test_custom_submit_and_close_labels(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test Modal",
            submit_label="Send",
            close_label="Dismiss",
            children=[],
        )
        view = modal_to_slack_view(modal)
        assert view["submit"] == {"type": "plain_text", "text": "Send"}
        assert view["close"] == {"type": "plain_text", "text": "Dismiss"}

    def test_multiline_text_input(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                TextInput(
                    id="description",
                    label="Description",
                    multiline=True,
                    placeholder="Enter description...",
                    max_length=500,
                )
            ],
        )
        view = modal_to_slack_view(modal)
        elem = view["blocks"][0]["element"]
        assert elem["type"] == "plain_text_input"
        assert elem["action_id"] == "description"
        assert elem["multiline"] is True
        assert elem["placeholder"] == {
            "type": "plain_text",
            "text": "Enter description...",
        }
        assert elem["max_length"] == 500

    def test_optional_text_input(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[TextInput(id="notes", label="Notes", optional=True)],
        )
        view = modal_to_slack_view(modal)
        assert view["blocks"][0]["type"] == "input"
        assert view["blocks"][0]["optional"] is True

    def test_text_input_with_initial_value(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[TextInput(id="name", label="Name", initial_value="John Doe")],
        )
        view = modal_to_slack_view(modal)
        assert view["blocks"][0]["element"]["initial_value"] == "John Doe"

    def test_select_with_options(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                Select(
                    id="category",
                    label="Category",
                    options=[
                        SelectOption(label="Bug Report", value="bug"),
                        SelectOption(label="Feature Request", value="feature"),
                    ],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        block = view["blocks"][0]
        assert block["type"] == "input"
        assert block["block_id"] == "category"
        assert block["label"] == {"type": "plain_text", "text": "Category"}
        elem = block["element"]
        assert elem["type"] == "static_select"
        assert elem["action_id"] == "category"
        assert elem["options"] == [
            {"text": {"type": "plain_text", "text": "Bug Report"}, "value": "bug"},
            {
                "text": {"type": "plain_text", "text": "Feature Request"},
                "value": "feature",
            },
        ]

    def test_select_with_initial_option(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                Select(
                    id="priority",
                    label="Priority",
                    options=[
                        SelectOption(label="Low", value="low"),
                        SelectOption(label="Medium", value="medium"),
                        SelectOption(label="High", value="high"),
                    ],
                    initial_option="medium",
                )
            ],
        )
        view = modal_to_slack_view(modal)
        elem = view["blocks"][0]["element"]
        assert elem["initial_option"] == {
            "text": {"type": "plain_text", "text": "Medium"},
            "value": "medium",
        }

    def test_select_with_placeholder(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                Select(
                    id="category",
                    label="Category",
                    placeholder="Select a category",
                    options=[SelectOption(label="General", value="general")],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        elem = view["blocks"][0]["element"]
        assert elem["placeholder"] == {
            "type": "plain_text",
            "text": "Select a category",
        }

    def test_external_select(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                ExternalSelect(
                    id="person",
                    label="Person",
                    placeholder="Search people",
                    min_query_length=1,
                )
            ],
        )
        view = modal_to_slack_view(modal)
        block = view["blocks"][0]
        assert block["type"] == "input"
        assert block["block_id"] == "person"
        assert block["label"] == {"type": "plain_text", "text": "Person"}
        elem = block["element"]
        assert elem["type"] == "external_select"
        assert elem["action_id"] == "person"
        assert elem["placeholder"] == {
            "type": "plain_text",
            "text": "Search people",
        }
        assert elem["min_query_length"] == 1

    def test_context_id_as_private_metadata(self) -> None:
        modal = Modal(callback_id="test", title="Test", children=[])
        view = modal_to_slack_view(modal, "context-uuid-123")
        assert view["private_metadata"] == "context-uuid-123"

    def test_private_metadata_omitted_when_no_context_id(self) -> None:
        modal = Modal(callback_id="test", title="Test", children=[])
        view = modal_to_slack_view(modal)
        assert "private_metadata" not in view

    def test_notify_on_close(self) -> None:
        modal = Modal(callback_id="test", title="Test", notify_on_close=True, children=[])
        view = modal_to_slack_view(modal)
        assert view["notify_on_close"] is True

    def test_truncates_long_titles_to_24_chars(self) -> None:
        modal = Modal(
            callback_id="test",
            title="This is a very long modal title that exceeds the limit",
            children=[],
        )
        view = modal_to_slack_view(modal)
        assert len(view["title"]["text"]) <= 24

    def test_complete_modal_with_multiple_inputs(self) -> None:
        modal = Modal(
            callback_id="feedback_form",
            title="Submit Feedback",
            submit_label="Send",
            close_label="Cancel",
            notify_on_close=True,
            children=[
                TextInput(
                    id="message",
                    label="Your Feedback",
                    placeholder="Tell us what you think...",
                    multiline=True,
                ),
                Select(
                    id="category",
                    label="Category",
                    options=[
                        SelectOption(label="Bug", value="bug"),
                        SelectOption(label="Feature", value="feature"),
                        SelectOption(label="Other", value="other"),
                    ],
                ),
                TextInput(id="email", label="Email (optional)", optional=True),
            ],
        )
        view = modal_to_slack_view(modal, "thread-context-123")
        assert view["callback_id"] == "feedback_form"
        assert view["private_metadata"] == "thread-context-123"
        assert len(view["blocks"]) == 3
        for block in view["blocks"]:
            assert block["type"] == "input"


# ---------------------------------------------------------------------------
# encode_modal_metadata
# ---------------------------------------------------------------------------


class TestEncodeModalMetadata:
    def test_returns_none_when_both_empty(self) -> None:
        assert encode_modal_metadata({}) is None

    def test_context_id_only(self) -> None:
        encoded = encode_modal_metadata({"contextId": "uuid-123"})
        assert encoded is not None
        parsed = json.loads(encoded)
        assert parsed["c"] == "uuid-123"
        assert parsed["m"] is None

    def test_private_metadata_only(self) -> None:
        encoded = encode_modal_metadata({"privateMetadata": '{"chatId":"abc"}'})
        assert encoded is not None
        parsed = json.loads(encoded)
        assert parsed["c"] is None
        assert parsed["m"] == '{"chatId":"abc"}'

    def test_both(self) -> None:
        encoded = encode_modal_metadata(
            {"contextId": "uuid-123", "privateMetadata": '{"chatId":"abc"}'}
        )
        assert encoded is not None
        parsed = json.loads(encoded)
        assert parsed["c"] == "uuid-123"
        assert parsed["m"] == '{"chatId":"abc"}'


# ---------------------------------------------------------------------------
# decode_modal_metadata
# ---------------------------------------------------------------------------


class TestDecodeModalMetadata:
    def test_none_input_returns_empty(self) -> None:
        assert decode_modal_metadata(None) == {}

    def test_empty_string_returns_empty(self) -> None:
        assert decode_modal_metadata("") == {}

    def test_context_id_only(self) -> None:
        encoded = json.dumps({"c": "uuid-123"})
        assert decode_modal_metadata(encoded) == {
            "contextId": "uuid-123",
            "privateMetadata": None,
        }

    def test_private_metadata_only(self) -> None:
        encoded = json.dumps({"m": '{"chatId":"abc"}'})
        assert decode_modal_metadata(encoded) == {
            "contextId": None,
            "privateMetadata": '{"chatId":"abc"}',
        }

    def test_both(self) -> None:
        encoded = json.dumps({"c": "uuid-123", "m": '{"chatId":"abc"}'})
        assert decode_modal_metadata(encoded) == {
            "contextId": "uuid-123",
            "privateMetadata": '{"chatId":"abc"}',
        }

    def test_plain_string_fallback(self) -> None:
        assert decode_modal_metadata("plain-uuid-456") == {
            "contextId": "plain-uuid-456",
        }

    def test_json_without_c_or_m(self) -> None:
        assert decode_modal_metadata('{"other":"value"}') == {
            "contextId": '{"other":"value"}',
        }

    def test_roundtrip(self) -> None:
        original = {
            "contextId": "ctx-1",
            "privateMetadata": '{"key":"val"}',
        }
        encoded = encode_modal_metadata(original)
        decoded = decode_modal_metadata(encoded)
        assert decoded == original


# ---------------------------------------------------------------------------
# Radio select
# ---------------------------------------------------------------------------


class TestRadioSelect:
    def test_converts_radio_select(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                RadioSelect(
                    id="plan",
                    label="Choose Plan",
                    options=[
                        SelectOption(label="Basic", value="basic"),
                        SelectOption(label="Pro", value="pro"),
                        SelectOption(label="Enterprise", value="enterprise"),
                    ],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        assert len(view["blocks"]) == 1
        block = view["blocks"][0]
        assert block["type"] == "input"
        assert block["block_id"] == "plan"
        assert block["label"] == {"type": "plain_text", "text": "Choose Plan"}
        elem = block["element"]
        assert elem["type"] == "radio_buttons"
        assert elem["action_id"] == "plan"
        assert len(elem["options"]) == 3

    def test_optional_radio_select(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                RadioSelect(
                    id="preference",
                    label="Preference",
                    optional=True,
                    options=[
                        SelectOption(label="Yes", value="yes"),
                        SelectOption(label="No", value="no"),
                    ],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        assert view["blocks"][0]["optional"] is True

    def test_mrkdwn_label_type(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                RadioSelect(
                    id="option",
                    label="Choose",
                    options=[SelectOption(label="Option A", value="a")],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        option = view["blocks"][0]["element"]["options"][0]
        assert option["text"]["type"] == "mrkdwn"
        assert option["text"]["text"] == "Option A"

    def test_limits_options_to_10(self) -> None:
        options = [SelectOption(label=f"Option {i + 1}", value=f"opt{i + 1}") for i in range(15)]
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[RadioSelect(id="many_options", label="Many Options", options=options)],
        )
        view = modal_to_slack_view(modal)
        assert len(view["blocks"][0]["element"]["options"]) == 10


# ---------------------------------------------------------------------------
# SelectOption descriptions
# ---------------------------------------------------------------------------


class TestSelectOptionDescriptions:
    def test_plain_text_description_in_select(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                Select(
                    id="plan",
                    label="Plan",
                    options=[
                        SelectOption(label="Basic", value="basic", description="For individuals"),
                        SelectOption(label="Pro", value="pro", description="For teams"),
                    ],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        options = view["blocks"][0]["element"]["options"]
        assert options[0]["description"] == {
            "type": "plain_text",
            "text": "For individuals",
        }
        assert options[1]["description"] == {
            "type": "plain_text",
            "text": "For teams",
        }

    def test_mrkdwn_description_in_radio_select(self) -> None:
        modal = Modal(
            callback_id="test",
            title="Test",
            children=[
                RadioSelect(
                    id="plan",
                    label="Plan",
                    options=[
                        SelectOption(
                            label="Basic",
                            value="basic",
                            description="For *individuals*",
                        ),
                        SelectOption(label="Pro", value="pro", description="For _teams_"),
                    ],
                )
            ],
        )
        view = modal_to_slack_view(modal)
        options = view["blocks"][0]["element"]["options"]
        assert options[0]["description"] == {
            "type": "mrkdwn",
            "text": "For *individuals*",
        }
        assert options[1]["description"] == {
            "type": "mrkdwn",
            "text": "For _teams_",
        }
