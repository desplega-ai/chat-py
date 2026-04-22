"""Env-flag gating helpers for integration tests.

Integration tests against live services (Slack, Teams, Redis, Postgres, …)
are opt-in. Each backend reads its own environment variable, and a missing
value produces a ``pytest.skip`` so the unit-test default remains green on
laptops and CI.

Conventions
-----------

- ``SLACK_TOKEN``     — bot token for live Slack tests
- ``TEAMS_APP_ID``    — app ID for live Microsoft Teams tests
- ``GCHAT_PROJECT``   — project ID for live Google Chat tests
- ``DISCORD_TOKEN``   — bot token for live Discord tests
- ``GITHUB_TOKEN``    — PAT for live GitHub tests
- ``LINEAR_API_KEY``  — API key for live Linear tests
- ``WHATSAPP_TOKEN``  — bot token for live WhatsApp tests
- ``TELEGRAM_TOKEN``  — bot token for live Telegram tests
- ``REDIS_URL``       — connection URL for live Redis tests (also covers ioredis)
- ``POSTGRES_URL``    — DSN for live Postgres tests
"""

from __future__ import annotations

import os

import pytest

# Keep names aligned with the per-adapter "create_*" factories so readers
# can guess the right flag without consulting this module.
ENV_FLAGS: dict[str, str] = {
    "slack": "SLACK_TOKEN",
    "teams": "TEAMS_APP_ID",
    "gchat": "GCHAT_PROJECT",
    "discord": "DISCORD_TOKEN",
    "github": "GITHUB_TOKEN",
    "linear": "LINEAR_API_KEY",
    "whatsapp": "WHATSAPP_TOKEN",
    "telegram": "TELEGRAM_TOKEN",
    "redis": "REDIS_URL",
    "ioredis": "REDIS_URL",
    "postgres": "POSTGRES_URL",
}


def require_env(flag: str) -> str:
    """Return the value of ``flag`` or skip the current test.

    ``pytest.skip`` raises ``Skipped`` internally — callers can use this
    wherever they would otherwise read ``os.environ[...]``.
    """

    value = os.environ.get(flag)
    if not value:
        pytest.skip(f"Set {flag} to enable this live integration test.")
    return value


def require_backend(backend: str) -> str:
    """Skip unless the env flag for ``backend`` is set.

    Example::

        url = require_backend("redis")  # reads REDIS_URL
    """

    flag = ENV_FLAGS.get(backend)
    if flag is None:
        raise KeyError(f"Unknown backend: {backend!r}")
    return require_env(flag)


def has_backend(backend: str) -> bool:
    """Return ``True`` when the env flag for ``backend`` is set."""

    flag = ENV_FLAGS.get(backend)
    if flag is None:
        return False
    return bool(os.environ.get(flag))


__all__ = ["ENV_FLAGS", "has_backend", "require_backend", "require_env"]
