"""Retry/backoff behavior of the shared HTTP helper."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from http_retry import request_with_retry


def _resp(status: int, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after else {}
    return resp


def test_success_first_try_makes_one_request():
    ok = _resp(200)
    with patch.object(httpx, "request", return_value=ok) as req, patch("http_retry.time.sleep"):
        resp = request_with_retry("GET", "https://x.test/a")
    assert resp is ok
    assert req.call_count == 1


def test_retries_on_429_then_succeeds():
    with (
        patch.object(httpx, "request", side_effect=[_resp(429), _resp(200)]) as req,
        patch("http_retry.time.sleep") as sleep,
    ):
        resp = request_with_retry("GET", "https://x.test/a")
    assert resp.status_code == 200
    assert req.call_count == 2
    assert sleep.call_count == 1


def test_honors_retry_after_header():
    with (
        patch.object(httpx, "request", side_effect=[_resp(429, retry_after="3"), _resp(200)]),
        patch("http_retry.time.sleep") as sleep,
    ):
        request_with_retry("GET", "https://x.test/a")
    assert sleep.call_args[0][0] == 3.0


def test_returns_last_error_response_after_max_attempts():
    with (
        patch.object(httpx, "request", side_effect=[_resp(503), _resp(503), _resp(503)]) as req,
        patch("http_retry.time.sleep"),
    ):
        resp = request_with_retry("GET", "https://x.test/a")
    assert resp.status_code == 503
    assert req.call_count == 3


def test_retries_connect_errors():
    ok = _resp(200)
    with (
        patch.object(httpx, "request", side_effect=[httpx.ConnectError("boom"), ok]) as req,
        patch("http_retry.time.sleep"),
    ):
        resp = request_with_retry("GET", "https://x.test/a")
    assert resp is ok
    assert req.call_count == 2


def test_raises_connect_error_after_max_attempts():
    with (
        patch.object(httpx, "request", side_effect=httpx.ConnectError("boom")),
        patch("http_retry.time.sleep"),
        pytest.raises(httpx.ConnectError),
    ):
        request_with_retry("GET", "https://x.test/a")


def test_non_idempotent_does_not_retry_statuses():
    """A POST must not be replayed on 5xx — the write may have gone through."""
    with (
        patch.object(httpx, "request", side_effect=[_resp(503), _resp(200)]) as req,
        patch("http_retry.time.sleep"),
    ):
        resp = request_with_retry("POST", "https://x.test/a", idempotent=False)
    assert resp.status_code == 503
    assert req.call_count == 1


def test_non_idempotent_does_not_retry_read_errors():
    with (
        patch.object(httpx, "request", side_effect=httpx.ReadTimeout("slow")),
        patch("http_retry.time.sleep"),
        pytest.raises(httpx.ReadTimeout),
    ):
        request_with_retry("POST", "https://x.test/a", idempotent=False)


def test_non_idempotent_still_retries_connect_errors():
    """Connect errors mean the request never reached the server — safe even for POST."""
    ok = _resp(200)
    with (
        patch.object(httpx, "request", side_effect=[httpx.ConnectError("down"), ok]) as req,
        patch("http_retry.time.sleep"),
    ):
        resp = request_with_retry("POST", "https://x.test/a", idempotent=False)
    assert resp is ok
    assert req.call_count == 2


def test_client_errors_are_not_retried():
    with patch.object(httpx, "request", return_value=_resp(404)) as req, patch("http_retry.time.sleep"):
        resp = request_with_retry("GET", "https://x.test/a")
    assert resp.status_code == 404
    assert req.call_count == 1
