"""Tests for :mod:`chat.errors`, ported from upstream ``errors.test.ts``."""

from __future__ import annotations

from chat.errors import ChatError, LockError, NotImplementedError, RateLimitError


class TestChatError:
    def test_sets_message_code_and_name(self) -> None:
        err = ChatError("something broke", "SOME_CODE")
        assert err.message == "something broke"
        assert err.code == "SOME_CODE"
        assert err.name == "ChatError"

    def test_is_subclass_of_exception(self) -> None:
        err = ChatError("fail", "ERR")
        assert isinstance(err, Exception)
        assert isinstance(err, ChatError)

    def test_propagates_cause(self) -> None:
        cause = Exception("root cause")
        err = ChatError("wrapped", "WRAP", cause)
        assert err.cause is cause

    def test_allows_undefined_cause(self) -> None:
        err = ChatError("no cause", "NC")
        assert err.cause is None


class TestRateLimitError:
    def test_sets_code_to_rate_limited(self) -> None:
        err = RateLimitError("slow down")
        assert err.code == "RATE_LIMITED"
        assert err.name == "RateLimitError"

    def test_stores_retry_after_ms(self) -> None:
        err = RateLimitError("slow down", 5000)
        assert err.retry_after_ms == 5000

    def test_allows_undefined_retry_after_ms(self) -> None:
        err = RateLimitError("slow down")
        assert err.retry_after_ms is None

    def test_is_chat_error_and_exception(self) -> None:
        err = RateLimitError("slow down")
        assert isinstance(err, ChatError)
        assert isinstance(err, Exception)

    def test_propagates_cause(self) -> None:
        cause = Exception("api error")
        err = RateLimitError("rate limited", 1000, cause)
        assert err.cause is cause


class TestLockError:
    def test_sets_code_to_lock_failed(self) -> None:
        err = LockError("lock failed")
        assert err.code == "LOCK_FAILED"
        assert err.name == "LockError"

    def test_is_chat_error(self) -> None:
        err = LockError("lock failed")
        assert isinstance(err, ChatError)

    def test_propagates_cause(self) -> None:
        cause = Exception("redis down")
        err = LockError("lock failed", cause)
        assert err.cause is cause


class TestNotImplementedError:
    def test_sets_code_to_not_implemented(self) -> None:
        err = NotImplementedError("not yet")
        assert err.code == "NOT_IMPLEMENTED"
        assert err.name == "NotImplementedError"

    def test_stores_feature_field(self) -> None:
        err = NotImplementedError("not yet", "reactions")
        assert err.feature == "reactions"

    def test_allows_undefined_feature(self) -> None:
        err = NotImplementedError("not yet")
        assert err.feature is None

    def test_is_chat_error(self) -> None:
        err = NotImplementedError("not yet")
        assert isinstance(err, ChatError)

    def test_propagates_cause(self) -> None:
        cause = Exception("underlying")
        err = NotImplementedError("not yet", "modals", cause)
        assert err.cause is cause
