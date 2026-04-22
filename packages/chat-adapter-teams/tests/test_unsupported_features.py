"""Pin the 7 Teams :class:`chat.NotImplementedError` stubs.

Mirrors upstream ``packages/adapter-teams/src/index.ts`` — these
methods intentionally raise with ``feature=<camelCase>`` so callers
can detect "the platform supports this but the Teams SDK hasn't
wired it up" and fall back.

If upstream eventually ports Graph API reader methods or reaction
support, flip the corresponding assertion to ``pytest.raises`` of
a real result — that's the signal the stub has been retired.
"""

from __future__ import annotations

import pytest
from chat.errors import NotImplementedError as ChatNotImplementedError
from chat_adapter_teams import create_teams_adapter
from chat_adapter_teams.adapter import TeamsAdapter


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> TeamsAdapter:
    monkeypatch.setenv("TEAMS_APP_ID", "test-app-id")
    return create_teams_adapter()


async def test_add_reaction_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.add_reaction("teams:x:y", "1", "thumbs_up")
    assert exc.value.feature == "addReaction"


async def test_remove_reaction_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.remove_reaction("teams:x:y", "1", "thumbs_up")
    assert exc.value.feature == "removeReaction"


async def test_fetch_messages_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.fetch_messages("teams:x:y")
    assert exc.value.feature == "fetchMessages"


async def test_fetch_thread_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.fetch_thread("teams:x:y")
    assert exc.value.feature == "fetchThread"


async def test_fetch_channel_messages_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.fetch_channel_messages("channel-x")
    assert exc.value.feature == "fetchChannelMessages"


async def test_list_threads_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.list_threads("channel-x")
    assert exc.value.feature == "listThreads"


async def test_fetch_channel_info_raises_chat_not_implemented(adapter: TeamsAdapter) -> None:
    with pytest.raises(ChatNotImplementedError) as exc:
        await adapter.fetch_channel_info("channel-x")
    assert exc.value.feature == "fetchChannelInfo"


def test_certificate_auth_rejected_at_construction() -> None:
    """Cert auth is deprecated upstream and unsupported here —
    construction must fail with :class:`ValidationError`."""

    from chat_adapter_shared import ValidationError
    from chat_adapter_teams.adapter import TeamsAuthCertificate

    with pytest.raises(ValidationError):
        create_teams_adapter(
            {"certificate": TeamsAuthCertificate(certificate_private_key="-----BEGIN...")}  # type: ignore[typeddict-item]
        )
