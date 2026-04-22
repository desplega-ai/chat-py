"""Integration tests for chat-py — cross-package adapter↔state↔core flows.

This package exercises real Chat/StateAdapter/Adapter wiring end-to-end. It
mirrors the upstream ``packages/chat/tests/integration`` suite: a handful of
happy-path "receive webhook → dispatch → store state → reply" scenarios per
state backend, plus error-path coverage for the main failure modes
(authentication, rate-limit, malformed payload).

Most tests run without external services by composing the core :class:`Chat`
with:

- :class:`chat_adapter_state_memory.MemoryStateAdapter` — in-process state,
- :mod:`fakeredis` — an in-process Redis implementation (for redis / ioredis),
- a mock ``asyncpg``-shaped pool (for the Postgres adapter),
- :func:`chat.mock_adapter.create_mock_adapter` — a duck-typed Adapter.

Tests that require a live service (Slack, Teams, real Redis/Postgres, …) are
gated behind environment flags — see :mod:`chat_integration_tests._env`. CI
runs the unit-test defaults; integration-only workflows set the flags to
exercise the live paths.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
