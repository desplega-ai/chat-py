"""Tests for :mod:`chat.reviver` and :mod:`chat._serde`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from chat._serde import SERDE_REGISTRY, chat_deserialize, chat_serialize
from chat.channel import ChannelImpl
from chat.message import Attachment, Author, Message, MessageMetadata
from chat.mock_adapter import create_mock_adapter, create_mock_state
from chat.reviver import object_hook, reviver
from chat.thread import ThreadImpl


def _make_message() -> Message[Any]:
    return Message(
        id="msg-1",
        thread_id="slack:C1:T1",
        text="hi",
        formatted={"type": "root", "children": []},
        raw=None,
        author=Author(
            user_id="U1",
            user_name="alice",
            full_name="Alice",
            is_bot=False,
            is_me=False,
        ),
        metadata=MessageMetadata(
            date_sent=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            edited=False,
            edited_at=None,
        ),
        attachments=[
            Attachment(type="file", url="https://x/x.txt", name="x.txt", mime_type="text/plain")
        ],
        links=[],
    )


def _make_channel() -> ChannelImpl[Any]:
    adapter = create_mock_adapter("slack")
    return ChannelImpl(
        id="C1",
        adapter_name="slack",
        adapter=adapter,
        state_adapter=create_mock_state(),
        channel_visibility="public",
        is_dm=False,
    )


def _make_thread() -> ThreadImpl[Any]:
    adapter = create_mock_adapter("slack")
    return ThreadImpl(
        id="slack:C1:T1",
        channel_id="C1",
        adapter=adapter,
        state_adapter=create_mock_state(),
        adapter_name="slack",
        channel_visibility="public",
        is_dm=False,
    )


class TestReviver:
    def test_revives_message(self) -> None:
        msg = _make_message()
        data = msg.to_json()
        restored = reviver("root", data)
        assert isinstance(restored, Message)
        assert restored.id == msg.id
        assert restored.text == msg.text

    def test_revives_channel(self) -> None:
        ch = _make_channel()
        data = ch.to_json()
        restored = reviver("root", data)
        assert isinstance(restored, ChannelImpl)
        assert restored.id == ch.id

    def test_revives_thread(self) -> None:
        t = _make_thread()
        data = t.to_json()
        restored = reviver("root", data)
        assert isinstance(restored, ThreadImpl)
        assert restored.id == t.id
        assert restored.channel_id == t.channel_id

    def test_untagged_dict_passthrough(self) -> None:
        assert reviver("x", {"foo": "bar"}) == {"foo": "bar"}

    def test_non_dict_passthrough(self) -> None:
        assert reviver("x", "plain") == "plain"
        assert reviver("x", 42) == 42
        assert reviver("x", None) is None
        assert reviver("x", [1, 2]) == [1, 2]

    def test_unknown_tag_passthrough(self) -> None:
        assert reviver("x", {"_type": "not:chat", "a": 1}) == {"_type": "not:chat", "a": 1}


class TestObjectHook:
    def test_json_loads_with_hook(self) -> None:
        msg = _make_message()
        blob = json.dumps(msg.to_json())
        restored = json.loads(blob, object_hook=object_hook)
        assert isinstance(restored, Message)
        assert restored.id == msg.id

    def test_nested_json_loads(self) -> None:
        msg = _make_message()
        payload = {"payload": msg.to_json(), "meta": {"v": 1}}
        blob = json.dumps(payload)
        out = json.loads(blob, object_hook=object_hook)
        assert isinstance(out["payload"], Message)
        assert out["meta"] == {"v": 1}


class TestSerdeRegistry:
    def test_registry_tags(self) -> None:
        assert set(SERDE_REGISTRY) == {"chat:Message", "chat:Channel", "chat:Thread"}

    def test_chat_serialize_message(self) -> None:
        msg = _make_message()
        data = chat_serialize(msg)
        assert data["_type"] == "chat:Message"
        assert data["id"] == msg.id

    def test_chat_serialize_thread(self) -> None:
        t = _make_thread()
        data = chat_serialize(t)
        assert data["_type"] == "chat:Thread"
        assert data["id"] == t.id

    def test_chat_serialize_channel(self) -> None:
        ch = _make_channel()
        data = chat_serialize(ch)
        assert data["_type"] == "chat:Channel"
        assert data["id"] == ch.id

    def test_chat_deserialize_roundtrip_message(self) -> None:
        msg = _make_message()
        data = chat_serialize(msg)
        restored = chat_deserialize(data)
        assert isinstance(restored, Message)
        assert restored.id == msg.id

    def test_chat_deserialize_roundtrip_channel(self) -> None:
        ch = _make_channel()
        data = chat_serialize(ch)
        restored = chat_deserialize(data)
        assert isinstance(restored, ChannelImpl)
        assert restored.id == ch.id

    def test_chat_deserialize_roundtrip_thread(self) -> None:
        t = _make_thread()
        data = chat_serialize(t)
        restored = chat_deserialize(data)
        assert isinstance(restored, ThreadImpl)
        assert restored.id == t.id

    def test_chat_serialize_passthrough_for_unknown(self) -> None:
        assert chat_serialize({"plain": "dict"}) == {"plain": "dict"}
        assert chat_serialize(42) == 42

    def test_chat_deserialize_passthrough_for_unknown(self) -> None:
        assert chat_deserialize({"plain": "dict"}) == {"plain": "dict"}
        assert chat_deserialize({"_type": "not:chat"}) == {"_type": "not:chat"}
        assert chat_deserialize(42) == 42
