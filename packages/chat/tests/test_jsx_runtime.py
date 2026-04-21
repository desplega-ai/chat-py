"""Tests for :mod:`chat.jsx_runtime`."""

from __future__ import annotations

from chat.jsx_runtime import (
    Button,
    Card,
    Field,
    Fields,
    Modal,
    Section,
    Text,
    TextInput,
    card,
    is_card_element,
    is_jsx,
    is_modal_element,
    to_card_element,
    to_modal_element,
)


class TestReexports:
    def test_builder_functions_available(self) -> None:
        c = Card(title="Hello", children=[Text("body")])
        assert c["type"] == "card"
        assert c["title"] == "Hello"

    def test_text_builder(self) -> None:
        t = Text("content")
        assert t["type"] == "text"
        assert t["content"] == "content"

    def test_modal_builder(self) -> None:
        m = Modal(callback_id="cb", title="Modal", children=[TextInput(id="i", label="L")])
        assert m["type"] == "modal"
        assert m["callbackId"] == "cb"


class TestGuards:
    def test_is_card_element(self) -> None:
        assert is_card_element(Card())
        assert not is_card_element({"type": "text"})
        assert not is_card_element(None)

    def test_is_modal_element(self) -> None:
        assert is_modal_element(Modal(callback_id="x", title="T", children=[]))
        assert not is_modal_element(Card())

    def test_is_jsx_accepts_card(self) -> None:
        assert is_jsx(Card())

    def test_is_jsx_accepts_modal(self) -> None:
        assert is_jsx(Modal(callback_id="x", title="T", children=[]))

    def test_is_jsx_accepts_children(self) -> None:
        assert is_jsx(Text("x"))
        assert is_jsx(Button(id="b", label="Click"))
        assert is_jsx(Field(label="L", value="V"))
        assert is_jsx(Section([Text("a")]))
        assert is_jsx(Fields([Field(label="a", value="b")]))

    def test_is_jsx_rejects_plain_dict(self) -> None:
        assert not is_jsx({"foo": "bar"})
        assert not is_jsx({})

    def test_is_jsx_rejects_non_dict(self) -> None:
        assert not is_jsx(None)
        assert not is_jsx("text")
        assert not is_jsx(42)


class TestCoercions:
    def test_to_card_element_card(self) -> None:
        c = Card(title="x")
        assert to_card_element(c) is c

    def test_to_card_element_non_card(self) -> None:
        assert to_card_element(Text("hi")) is None
        assert to_card_element(None) is None

    def test_to_modal_element_modal(self) -> None:
        m = Modal(callback_id="x", title="T", children=[])
        assert to_modal_element(m) is m

    def test_to_modal_element_non_modal(self) -> None:
        assert to_modal_element(Card()) is None


class TestCardDecorator:
    def test_marks_function(self) -> None:
        @card
        def builder() -> dict:
            return {"type": "card"}

        assert getattr(builder, "__chat_card__", False) is True

    def test_preserves_callability(self) -> None:
        @card
        def builder() -> dict:
            return Card(title="x")

        result = builder()
        assert result["type"] == "card"
