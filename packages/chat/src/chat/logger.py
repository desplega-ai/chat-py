"""Logger types and implementations for chat-sdk.

Python port of upstream ``packages/chat/src/logger.ts``. The upstream logger
writes directly to ``console.{debug,info,warn,error}`` with an inline prefix;
this port mirrors the same contract using :mod:`sys` streams so tests can
monkeypatch the standard ``print``-style I/O without depending on the logging
module's formatter machinery.
"""

from __future__ import annotations

import sys
from typing import Any, Literal, Protocol, runtime_checkable

LogLevel = Literal["debug", "info", "warn", "error", "silent"]

_LEVEL_ORDER: tuple[LogLevel, ...] = ("debug", "info", "warn", "error", "silent")


@runtime_checkable
class Logger(Protocol):
    """Structural logger interface matching upstream ``Logger``."""

    def child(self, prefix: str) -> Logger: ...

    def debug(self, message: str, *args: object) -> None: ...

    def info(self, message: str, *args: object) -> None: ...

    def warn(self, message: str, *args: object) -> None: ...

    def error(self, message: str, *args: object) -> None: ...


def _console_write(stream_name: str, parts: tuple[Any, ...]) -> None:
    """Emit ``parts`` to the JS-style ``console`` stream whose name is ``stream_name``.

    Upstream uses ``console.debug``/``console.info``/``console.warn``/``console.error``.
    In Python the closest analog is writing to ``sys.stderr`` (for warn/error) or
    ``sys.stdout`` (for debug/info). We route debug/info to stdout and warn/error
    to stderr to match what Node does by default, but the contract tests care
    about is: "the message + passthrough args are forwarded as-is".
    """

    # Keep the console namespace explicit in module scope so tests can
    # monkeypatch ``chat.logger.console``.
    target = console
    fn = getattr(target, stream_name)
    fn(*parts)


class _Console:
    """Small shim mirroring the JS ``console`` interface.

    Exposed as the module-level :data:`console` so tests can replace individual
    method attributes to capture calls (the Python equivalent of
    ``vi.spyOn(console, "info")``).
    """

    def debug(self, *args: Any) -> None:
        print(*args, file=sys.stdout)

    def info(self, *args: Any) -> None:
        print(*args, file=sys.stdout)

    def warn(self, *args: Any) -> None:
        print(*args, file=sys.stderr)

    def error(self, *args: Any) -> None:
        print(*args, file=sys.stderr)


console = _Console()


class ConsoleLogger:
    """Default console logger implementation.

    Mirrors upstream ``ConsoleLogger`` exactly: inline ``[prefix] message``
    formatting, with ``child`` appending ``:subprefix`` and passthrough of
    positional extra arguments.
    """

    def __init__(self, level: LogLevel = "info", prefix: str = "chat-sdk") -> None:
        self._level: LogLevel = level
        self._prefix: str = prefix

    @property
    def level(self) -> LogLevel:
        return self._level

    @property
    def prefix(self) -> str:
        return self._prefix

    def _should_log(self, level: LogLevel) -> bool:
        return _LEVEL_ORDER.index(level) >= _LEVEL_ORDER.index(self._level)

    def child(self, prefix: str) -> ConsoleLogger:
        return ConsoleLogger(self._level, f"{self._prefix}:{prefix}")

    def debug(self, message: str, *args: object) -> None:
        if self._should_log("debug"):
            _console_write("debug", (f"[{self._prefix}] {message}", *args))

    def info(self, message: str, *args: object) -> None:
        if self._should_log("info"):
            _console_write("info", (f"[{self._prefix}] {message}", *args))

    def warn(self, message: str, *args: object) -> None:
        if self._should_log("warn"):
            _console_write("warn", (f"[{self._prefix}] {message}", *args))

    def error(self, message: str, *args: object) -> None:
        if self._should_log("error"):
            _console_write("error", (f"[{self._prefix}] {message}", *args))
