"""Linear adapter structural conformance against ``chat.types.Adapter``.

Part of DES-196 Phase 8. The Linear port ships ``add_reaction`` as a
full implementation (Linear GraphQL ``reactionCreate``) and
``remove_reaction`` as a :class:`chat.NotImplementedError` stub — Linear's
GraphQL surface requires a reaction-id lookup that upstream does not
implement either. Conformance only requires the methods *exist* with the
expected signatures; runtime stub behaviour is pinned elsewhere.
"""

from __future__ import annotations

import pytest
from chat.types import Adapter
from chat_adapter_linear import create_linear_adapter


def test_linear_adapter_implements_adapter_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    adapter = create_linear_adapter()
    assert isinstance(adapter, Adapter), "LinearAdapter missing Protocol methods"
