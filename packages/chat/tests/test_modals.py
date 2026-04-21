"""Tests for :mod:`chat.modals` — mirrors upstream ``modals.test.ts``."""

from __future__ import annotations

import logging

import pytest
from chat.modals import (
    Modal,
    RadioSelect,
    Select,
    SelectOption,
    TextInput,
    filter_modal_children,
    is_modal_element,
)

# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------


class TestModal:
    def test_required_fields(self) -> None:
        modal = Modal(callback_id="cb-1", title="My Modal")
        assert modal["type"] == "modal"
        assert modal["callbackId"] == "cb-1"
        assert modal["title"] == "My Modal"
        assert modal["children"] == []

    def test_optional_fields(self) -> None:
        modal = Modal(
            callback_id="cb-1",
            title="Test",
            submit_label="Submit",
            close_label="Cancel",
            notify_on_close=True,
            private_metadata='{"key":"val"}',
        )
        assert modal["submitLabel"] == "Submit"
        assert modal["closeLabel"] == "Cancel"
        assert modal["notifyOnClose"] is True
        assert modal["privateMetadata"] == '{"key":"val"}'

    def test_accepts_children(self) -> None:
        inp = TextInput(id="t1", label="Name")
        modal = Modal(callback_id="cb-1", title="Test", children=[inp])
        assert len(modal["children"]) == 1
        assert modal["children"][0] == inp


# ---------------------------------------------------------------------------
# TextInput
# ---------------------------------------------------------------------------


class TestTextInput:
    def test_required_fields(self) -> None:
        inp = TextInput(id="t1", label="Name")
        assert inp["type"] == "text_input"
        assert inp["id"] == "t1"
        assert inp["label"] == "Name"

    def test_optional_fields(self) -> None:
        inp = TextInput(
            id="t1",
            label="Name",
            placeholder="Enter name",
            initial_value="John",
            multiline=True,
            optional=True,
            max_length=100,
        )
        assert inp["placeholder"] == "Enter name"
        assert inp["initialValue"] == "John"
        assert inp["multiline"] is True
        assert inp["optional"] is True
        assert inp["maxLength"] == 100


# ---------------------------------------------------------------------------
# Select / SelectOption
# ---------------------------------------------------------------------------


class TestSelect:
    def test_creates_with_options(self) -> None:
        sel = Select(
            id="s1",
            label="Pick one",
            options=[SelectOption(label="A", value="a")],
        )
        assert sel["type"] == "select"
        assert len(sel["options"]) == 1

    def test_raises_with_empty_options(self) -> None:
        with pytest.raises(ValueError, match="at least one option"):
            Select(id="s1", label="Pick", options=[])

    def test_optional_fields(self) -> None:
        sel = Select(
            id="s1",
            label="Pick",
            placeholder="Choose",
            options=[SelectOption(label="A", value="a")],
            initial_option="a",
            optional=True,
        )
        assert sel["placeholder"] == "Choose"
        assert sel["initialOption"] == "a"
        assert sel["optional"] is True


class TestSelectOption:
    def test_label_and_value(self) -> None:
        opt = SelectOption(label="Option A", value="a")
        assert opt["label"] == "Option A"
        assert opt["value"] == "a"

    def test_with_description(self) -> None:
        opt = SelectOption(label="Option A", value="a", description="First option")
        assert opt["description"] == "First option"


# ---------------------------------------------------------------------------
# RadioSelect
# ---------------------------------------------------------------------------


class TestRadioSelect:
    def test_creates_with_options(self) -> None:
        radio = RadioSelect(
            id="r1",
            label="Choose",
            options=[SelectOption(label="X", value="x")],
        )
        assert radio["type"] == "radio_select"
        assert len(radio["options"]) == 1

    def test_raises_with_empty_options(self) -> None:
        with pytest.raises(ValueError, match="at least one option"):
            RadioSelect(id="r1", label="Choose", options=[])


# ---------------------------------------------------------------------------
# Type guards
# ---------------------------------------------------------------------------


class TestIsModalElement:
    def test_true_for_modal(self) -> None:
        modal = Modal(callback_id="cb", title="T")
        assert is_modal_element(modal) is True

    @pytest.mark.parametrize(
        "value",
        [None, "string", 42, {"type": "text_input"}],
    )
    def test_false_for_non_modal(self, value: object) -> None:
        assert is_modal_element(value) is False


class TestFilterModalChildren:
    def test_keeps_valid_child_types(self) -> None:
        children = [
            TextInput(id="t1", label="Name"),
            Select(
                id="s1",
                label="Pick",
                options=[SelectOption(label="A", value="a")],
            ),
        ]
        result = filter_modal_children(list(children))
        assert len(result) == 2

    def test_filters_invalid_children_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="chat.modals")
        children = [
            TextInput(id="t1", label="Name"),
            {"type": "unknown_widget"},
        ]
        result = filter_modal_children(children)
        assert len(result) == 1
        assert any("unsupported child elements" in record.message for record in caplog.records)

    def test_filters_non_object_items(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="chat.modals")
        result = filter_modal_children(["string", None, 42])
        assert result == []
