"""Teams adapter structural conformance against ``chat.types.Adapter``.

Part of DES-196 Phase 7. The Teams port intentionally ships several
methods as :class:`chat.NotImplementedError` stubs (reactions, Graph
reader methods, certificate auth); conformance only requires the
methods *exist* with the expected signatures — runtime calls to stubs
may raise, that's covered by ``test_unsupported_features.py``.
"""

from __future__ import annotations

import pytest
from chat.types import Adapter
from chat_adapter_teams import create_teams_adapter


def test_teams_adapter_implements_adapter_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEAMS_APP_ID", "test-app-id")
    monkeypatch.setenv("TEAMS_APP_PASSWORD", "test-password")
    adapter = create_teams_adapter()
    assert isinstance(adapter, Adapter), "TeamsAdapter missing Protocol methods"
