"""Tests for :mod:`chat.logger`, ported from upstream ``logger.test.ts``."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from chat import logger as logger_mod
from chat.logger import ConsoleLogger


class _Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> None:
        self.calls.append(args)

    @property
    def called(self) -> bool:
        return len(self.calls) > 0


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, _Spy]]:
    debug = _Spy()
    info = _Spy()
    warn = _Spy()
    error = _Spy()
    monkeypatch.setattr(logger_mod.console, "debug", debug)
    monkeypatch.setattr(logger_mod.console, "info", info)
    monkeypatch.setattr(logger_mod.console, "warn", warn)
    monkeypatch.setattr(logger_mod.console, "error", error)
    yield {"debug": debug, "info": info, "warn": warn, "error": error}


class TestDefaultLevelInfo:
    def test_does_not_log_debug(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger()
        logger.debug("hidden")
        assert not spies["debug"].called

    def test_logs_info(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger()
        logger.info("visible")
        assert spies["info"].calls == [("[chat-sdk] visible",)]

    def test_logs_warn(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger()
        logger.warn("warning")
        assert spies["warn"].calls == [("[chat-sdk] warning",)]

    def test_logs_error(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger()
        logger.error("failure")
        assert spies["error"].calls == [("[chat-sdk] failure",)]


class TestDebugLevel:
    def test_logs_all_levels(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("debug")
        logger.debug("dbg")
        logger.info("inf")
        logger.warn("wrn")
        logger.error("err")
        assert spies["debug"].called
        assert spies["info"].called
        assert spies["warn"].called
        assert spies["error"].called


class TestWarnLevel:
    def test_only_warn_and_error(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("warn")
        logger.debug("hidden")
        logger.info("hidden")
        logger.warn("visible")
        logger.error("visible")
        assert not spies["debug"].called
        assert not spies["info"].called
        assert spies["warn"].called
        assert spies["error"].called


class TestErrorLevel:
    def test_only_errors(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("error")
        logger.debug("hidden")
        logger.info("hidden")
        logger.warn("hidden")
        logger.error("visible")
        assert not spies["debug"].called
        assert not spies["info"].called
        assert not spies["warn"].called
        assert spies["error"].called


class TestSilentLevel:
    def test_no_output(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("silent")
        logger.debug("hidden")
        logger.info("hidden")
        logger.warn("hidden")
        logger.error("hidden")
        assert not spies["debug"].called
        assert not spies["info"].called
        assert not spies["warn"].called
        assert not spies["error"].called


class TestPrefixFormatting:
    def test_default_prefix(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("info")
        logger.info("test")
        assert spies["info"].calls == [("[chat-sdk] test",)]

    def test_custom_prefix(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("info", "my-app")
        logger.info("test")
        assert spies["info"].calls == [("[my-app] test",)]


class TestExtraArgsPassthrough:
    def test_forwards_extra_args(self, spies: dict[str, _Spy]) -> None:
        logger = ConsoleLogger("debug")
        extra = {"key": "value"}
        logger.debug("msg", extra, 42)
        assert spies["debug"].calls == [("[chat-sdk] msg", extra, 42)]


class TestChildLogger:
    def test_combined_prefix(self, spies: dict[str, _Spy]) -> None:
        parent = ConsoleLogger("info", "parent")
        child = parent.child("child")
        child.info("test")
        assert spies["info"].calls == [("[parent:child] test",)]

    def test_inherits_level(self, spies: dict[str, _Spy]) -> None:
        parent = ConsoleLogger("warn", "parent")
        child = parent.child("child")
        child.info("hidden")
        child.warn("visible")
        assert not spies["info"].called
        assert spies["warn"].calls == [("[parent:child] visible",)]
