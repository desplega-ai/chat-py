"""Tests for :mod:`chat_adapter_slack.cards`."""

from __future__ import annotations

from chat_adapter_slack.cards import card_to_block_kit, card_to_fallback_text

from ._builders import (
    Actions,
    Button,
    Card,
    CardLink,
    CardText,
    Divider,
    Field,
    Fields,
    Image,
    LinkButton,
    RadioSelect,
    Section,
    Select,
    SelectOption,
    Table,
)


class TestCardToBlockKit:
    def test_converts_a_simple_card_with_title(self) -> None:
        card = Card(title="Welcome")
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0] == {
            "type": "header",
            "text": {"type": "plain_text", "text": "Welcome", "emoji": True},
        }

    def test_converts_a_card_with_title_and_subtitle(self) -> None:
        card = Card(title="Order Update", subtitle="Your order is on its way")
        blocks = card_to_block_kit(card)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "header"
        assert blocks[1] == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Your order is on its way"}],
        }

    def test_converts_a_card_with_header_image(self) -> None:
        card = Card(title="Product", imageUrl="https://example.com/product.png")
        blocks = card_to_block_kit(card)
        assert len(blocks) == 2
        assert blocks[1] == {
            "type": "image",
            "image_url": "https://example.com/product.png",
            "alt_text": "Product",
        }

    def test_converts_text_elements(self) -> None:
        card = Card(
            children=[
                CardText("Regular text"),
                CardText("Bold text", style="bold"),
                CardText("Muted text", style="muted"),
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 3

        assert blocks[0] == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Regular text"},
        }
        assert blocks[1] == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Bold text*"},
        }
        assert blocks[2] == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Muted text"}],
        }

    def test_converts_image_elements(self) -> None:
        card = Card(children=[Image(url="https://example.com/img.png", alt="My image")])
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0] == {
            "type": "image",
            "image_url": "https://example.com/img.png",
            "alt_text": "My image",
        }

    def test_converts_divider_elements(self) -> None:
        card = Card(children=[Divider()])
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0] == {"type": "divider"}

    def test_converts_actions_with_buttons(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Button(id="approve", label="Approve", style="primary"),
                        Button(
                            id="reject",
                            label="Reject",
                            style="danger",
                            value="data-123",
                        ),
                        Button(id="skip", label="Skip"),
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"

        elements = blocks[0]["elements"]
        assert len(elements) == 3

        assert elements[0] == {
            "type": "button",
            "text": {"type": "plain_text", "text": "Approve", "emoji": True},
            "action_id": "approve",
            "style": "primary",
        }
        assert elements[1] == {
            "type": "button",
            "text": {"type": "plain_text", "text": "Reject", "emoji": True},
            "action_id": "reject",
            "value": "data-123",
            "style": "danger",
        }
        assert elements[2] == {
            "type": "button",
            "text": {"type": "plain_text", "text": "Skip", "emoji": True},
            "action_id": "skip",
        }

    def test_converts_link_buttons_with_url_property(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        LinkButton(
                            url="https://example.com/docs",
                            label="View Docs",
                            style="primary",
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"
        elements = blocks[0]["elements"]
        assert len(elements) == 1
        assert elements[0]["type"] == "button"
        assert elements[0]["text"] == {
            "type": "plain_text",
            "text": "View Docs",
            "emoji": True,
        }
        assert elements[0]["url"] == "https://example.com/docs"
        assert elements[0]["style"] == "primary"

    def test_converts_fields(self) -> None:
        card = Card(
            children=[
                Fields(
                    [
                        Field(label="Status", value="Active"),
                        Field(label="Priority", value="High"),
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["fields"] == [
            {"type": "mrkdwn", "text": "*Status*\nActive"},
            {"type": "mrkdwn", "text": "*Priority*\nHigh"},
        ]

    def test_flattens_section_children(self) -> None:
        card = Card(children=[Section([CardText("Inside section"), Divider()])])
        blocks = card_to_block_kit(card)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "section"
        assert blocks[1]["type"] == "divider"

    def test_converts_a_complete_card(self) -> None:
        card = Card(
            title="Order #1234",
            subtitle="Status update",
            children=[
                CardText("Your order has been shipped!"),
                Divider(),
                Fields(
                    [
                        Field(label="Tracking", value="ABC123"),
                        Field(label="ETA", value="Dec 25"),
                    ]
                ),
                Actions([Button(id="track", label="Track Package", style="primary")]),
            ],
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 6
        types = [b["type"] for b in blocks]
        assert types == [
            "header",
            "context",
            "section",
            "divider",
            "section",
            "actions",
        ]


class TestCardToFallbackText:
    def test_generates_fallback_text_for_a_card(self) -> None:
        card = Card(
            title="Order Update",
            subtitle="Status changed",
            children=[
                CardText("Your order is ready"),
                Fields(
                    [
                        Field(label="Order ID", value="#1234"),
                        Field(label="Status", value="Ready"),
                    ]
                ),
                Actions(
                    [
                        Button(id="pickup", label="Schedule Pickup"),
                        Button(id="delay", label="Delay"),
                    ]
                ),
            ],
        )
        text = card_to_fallback_text(card)
        assert "*Order Update*" in text
        assert "Status changed" in text
        assert "Your order is ready" in text
        assert "Order ID: #1234" in text
        assert "Status: Ready" in text
        assert "[Schedule Pickup]" not in text
        assert "[Delay]" not in text

    def test_handles_card_with_only_title(self) -> None:
        card = Card(title="Simple Card")
        text = card_to_fallback_text(card)
        assert text == "*Simple Card*"


class TestCardToBlockKitWithSelectElements:
    def test_converts_actions_with_select_element(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="priority",
                            label="Priority",
                            placeholder="Select priority",
                            options=[
                                SelectOption(label="High", value="high"),
                                SelectOption(label="Medium", value="medium"),
                                SelectOption(label="Low", value="low"),
                            ],
                            initialOption="medium",
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"
        elements = blocks[0]["elements"]
        assert len(elements) == 1
        assert elements[0]["type"] == "static_select"
        assert elements[0]["action_id"] == "priority"
        assert elements[0]["placeholder"] == {
            "type": "plain_text",
            "text": "Select priority",
        }
        assert len(elements[0]["options"]) == 3
        assert elements[0]["options"][0] == {
            "text": {"type": "plain_text", "text": "High"},
            "value": "high",
        }
        assert elements[0]["initial_option"] == {
            "text": {"type": "plain_text", "text": "Medium"},
            "value": "medium",
        }

    def test_converts_actions_with_mixed_buttons_and_selects(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="status",
                            label="Status",
                            options=[
                                SelectOption(label="Open", value="open"),
                                SelectOption(label="Closed", value="closed"),
                            ],
                        ),
                        Button(id="submit", label="Submit", style="primary"),
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        elements = blocks[0]["elements"]
        assert len(elements) == 2
        assert elements[0]["type"] == "static_select"
        assert elements[0]["action_id"] == "status"
        assert elements[1]["type"] == "button"
        assert elements[1]["action_id"] == "submit"

    def test_converts_select_without_placeholder_or_initial_option(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="category",
                            label="Category",
                            options=[
                                SelectOption(label="Bug", value="bug"),
                                SelectOption(label="Feature", value="feature"),
                            ],
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        elements = blocks[0]["elements"]
        assert elements[0]["type"] == "static_select"
        assert "placeholder" not in elements[0]
        assert "initial_option" not in elements[0]


class TestCardToBlockKitWithRadioSelectElements:
    def test_converts_actions_with_radio_select_element(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        RadioSelect(
                            id="plan",
                            label="Choose Plan",
                            options=[
                                SelectOption(label="Basic", value="basic"),
                                SelectOption(label="Pro", value="pro"),
                                SelectOption(label="Enterprise", value="enterprise"),
                            ],
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "actions"
        elements = blocks[0]["elements"]
        assert len(elements) == 1
        assert elements[0]["type"] == "radio_buttons"
        assert elements[0]["action_id"] == "plan"
        assert len(elements[0]["options"]) == 3

    def test_uses_mrkdwn_type_for_radio_select_labels(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        RadioSelect(
                            id="option",
                            label="Choose",
                            options=[SelectOption(label="Option A", value="a")],
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        elements = blocks[0]["elements"]
        assert elements[0]["options"][0]["text"]["type"] == "mrkdwn"
        assert elements[0]["options"][0]["text"]["text"] == "Option A"

    def test_limits_radio_select_options_to_10(self) -> None:
        options = [SelectOption(label=f"Option {i + 1}", value=f"opt{i + 1}") for i in range(15)]
        card = Card(
            children=[
                Actions(
                    [
                        RadioSelect(
                            id="many_options",
                            label="Many Options",
                            options=options,
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        elements = blocks[0]["elements"]
        assert len(elements[0]["options"]) == 10


class TestCardToBlockKitWithSelectOptionDescriptions:
    def test_includes_description_in_select_options_with_plain_text_type(
        self,
    ) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="plan",
                            label="Plan",
                            options=[
                                SelectOption(
                                    label="Basic",
                                    value="basic",
                                    description="For individuals",
                                ),
                                SelectOption(
                                    label="Pro",
                                    value="pro",
                                    description="For teams",
                                ),
                            ],
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        elements = blocks[0]["elements"]
        assert elements[0]["options"][0]["description"] == {
            "type": "plain_text",
            "text": "For individuals",
        }
        assert elements[0]["options"][1]["description"] == {
            "type": "plain_text",
            "text": "For teams",
        }

    def test_includes_description_in_radio_select_options_with_mrkdwn_type(
        self,
    ) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        RadioSelect(
                            id="plan",
                            label="Plan",
                            options=[
                                SelectOption(
                                    label="Basic",
                                    value="basic",
                                    description="For *individuals*",
                                ),
                                SelectOption(
                                    label="Pro",
                                    value="pro",
                                    description="For _teams_",
                                ),
                            ],
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        elements = blocks[0]["elements"]
        assert elements[0]["options"][0]["description"] == {
            "type": "mrkdwn",
            "text": "For *individuals*",
        }
        assert elements[0]["options"][1]["description"] == {
            "type": "mrkdwn",
            "text": "For _teams_",
        }

    def test_omits_description_when_not_provided(self) -> None:
        card = Card(
            children=[
                Actions(
                    [
                        Select(
                            id="category",
                            label="Category",
                            options=[
                                SelectOption(label="Bug", value="bug"),
                                SelectOption(label="Feature", value="feature"),
                            ],
                        )
                    ]
                )
            ]
        )
        blocks = card_to_block_kit(card)
        elements = blocks[0]["elements"]
        assert "description" not in elements[0]["options"][0]
        assert "description" not in elements[0]["options"][1]


class TestMarkdownBoldToSlackMrkdwnConversion:
    def test_converts_double_asterisk_bold_to_single_in_cardtext(self) -> None:
        card = Card(children=[CardText("The **domain** is example.com")])
        blocks = card_to_block_kit(card)
        assert blocks[0] == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "The *domain* is example.com"},
        }

    def test_converts_multiple_bold_segments_in_one_cardtext(self) -> None:
        card = Card(
            children=[CardText("**Project**: my-app, **Status**: active, **Branch**: main")]
        )
        blocks = card_to_block_kit(card)
        assert blocks[0]["text"]["text"] == "*Project*: my-app, *Status*: active, *Branch*: main"

    def test_converts_bold_across_multiple_lines(self) -> None:
        card = Card(
            children=[
                CardText("**Domain**: example.com\n**Project**: my-app\n**Status**: deployed")
            ]
        )
        blocks = card_to_block_kit(card)
        assert (
            blocks[0]["text"]["text"]
            == "*Domain*: example.com\n*Project*: my-app\n*Status*: deployed"
        )

    def test_preserves_existing_single_asterisk_formatting(self) -> None:
        card = Card(children=[CardText("Already *bold* in Slack format")])
        blocks = card_to_block_kit(card)
        assert blocks[0]["text"]["text"] == "Already *bold* in Slack format"

    def test_handles_text_with_no_markdown_formatting(self) -> None:
        card = Card(children=[CardText("Plain text with no formatting")])
        blocks = card_to_block_kit(card)
        assert blocks[0]["text"]["text"] == "Plain text with no formatting"

    def test_converts_bold_in_muted_style_cardtext(self) -> None:
        card = Card(children=[CardText("Info about **thing**", style="muted")])
        blocks = card_to_block_kit(card)
        assert blocks[0] == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Info about *thing*"}],
        }

    def test_converts_bold_in_field_values(self) -> None:
        card = Card(children=[Fields([Field(label="Status", value="**Active**")])])
        blocks = card_to_block_kit(card)
        assert "*Active*" in blocks[0]["fields"][0]["text"]
        assert "**Active**" not in blocks[0]["fields"][0]["text"]

    def test_does_not_convert_empty_double_asterisks(self) -> None:
        card = Card(children=[CardText("text **** more")])
        blocks = card_to_block_kit(card)
        # **** has nothing between them, regex requires .+ so no conversion
        assert blocks[0]["text"]["text"] == "text **** more"

    def test_handles_bold_at_start_and_end_of_content(self) -> None:
        card = Card(children=[CardText("**Start** and **end**")])
        blocks = card_to_block_kit(card)
        assert blocks[0]["text"]["text"] == "*Start* and *end*"


class TestCardToBlockKitWithCardLink:
    def test_converts_cardlink_to_a_mrkdwn_section_block_with_slack_link_syntax(
        self,
    ) -> None:
        card = Card(children=[CardLink(url="https://example.com", label="Click here")])
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0] == {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "<https://example.com|Click here>",
            },
        }

    def test_converts_cardlink_alongside_other_children(self) -> None:
        card = Card(
            title="Test",
            children=[
                CardText("Hello"),
                CardLink(url="https://example.com", label="Link"),
            ],
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 3
        assert blocks[2] == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "<https://example.com|Link>"},
        }

    def test_converts_a_card_with_table_element_to_block_kit_table(self) -> None:
        card = Card(
            children=[
                Table(
                    headers=["Name", "Age"],
                    rows=[["Alice", "30"], ["Bob", "25"]],
                )
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "table"
        assert blocks[0]["rows"] == [
            [
                {"type": "raw_text", "text": "Name"},
                {"type": "raw_text", "text": "Age"},
            ],
            [
                {"type": "raw_text", "text": "Alice"},
                {"type": "raw_text", "text": "30"},
            ],
            [
                {"type": "raw_text", "text": "Bob"},
                {"type": "raw_text", "text": "25"},
            ],
        ]

    def test_falls_back_to_ascii_for_second_table_in_same_card(self) -> None:
        card = Card(
            children=[
                Table(headers=["A"], rows=[["1"]]),
                Table(headers=["B"], rows=[["2"]]),
            ]
        )
        blocks = card_to_block_kit(card)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "table"
        assert blocks[1]["type"] == "section"
        assert "```" in blocks[1]["text"]["text"]

    def test_replaces_empty_table_cells_with_a_space_to_satisfy_slack_api(
        self,
    ) -> None:
        card = Card(
            children=[
                Table(
                    headers=["Kind", ""],
                    rows=[
                        ["FORM", "Form Submission"],
                        ["and more...", ""],
                    ],
                )
            ]
        )
        blocks = card_to_block_kit(card)
        table_block = blocks[0]
        assert table_block["type"] == "table"
        for row in table_block["rows"]:
            for cell in row:
                assert len(cell["text"]) > 0
        assert table_block["rows"][0][1]["text"] == " "
        assert table_block["rows"][2][1]["text"] == " "
