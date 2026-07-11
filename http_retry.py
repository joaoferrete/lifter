"""Shared HTTP helper: per-request timeout + bounded retry with backoff.

Used by both API clients (hevy.client, fit.client). Transient failures —
429, 5xx, connection/timeout errors — are retried a couple of times with
exponential backoff and jitter, honoring Retry-After when the server sends
one. Anything still failing after the last attempt surfaces to the caller,
whose error mapping produces the user-facing message.

Non-idempotent requests (POST/PUT) only retry errors raised before the
request reached the server (connect errors), never response statuses — a 5xx
may have already executed the write.
"""

import contextlib
import random
import time

import httpx

DEFAULT_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 20.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def request_with_retry(
    method: str,
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_attempts: int = MAX_ATTEMPTS,
    idempotent: bool = True,
    **kwargs: object,
) -> httpx.Response:
    """httpx.request with timeout and transient-failure retry.

    Returns the last response (possibly an error response — status handling
    stays with the caller); raises the transport error if every attempt
    failed to get a response at all.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            resp = httpx.request(method, url, timeout=timeout, **kwargs)  # type: ignore[arg-type]
        except httpx.ConnectError as e:
            # Request never reached the server — always safe to retry.
            if attempt == max_attempts:
                raise
            _sleep_backoff(attempt, None, exc=e)
            continue
        except httpx.TransportError as e:
            # Read/write errors: the server may have processed the request.
            if not idempotent or attempt == max_attempts:
                raise
            _sleep_backoff(attempt, None, exc=e)
            continue

        if idempotent and resp.status_code in _RETRYABLE_STATUSES and attempt < max_attempts:
            _sleep_backoff(attempt, resp.headers.get("Retry-After"))
            continue
        return resp

    raise AssertionError("unreachable")  # pragma: no cover


def _sleep_backoff(attempt: int, retry_after: str | None, exc: Exception | None = None) -> None:
    delay = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** (attempt - 1))) + random.uniform(0, 0.5)
    if retry_after:
        with contextlib.suppress(ValueError):
            delay = min(float(retry_after), _BACKOFF_CAP_S)
    from debug_log import log

    log(
        "HTTP",
        "transient failure, retrying",
        attempt=attempt,
        delay=round(delay, 1),
        exc=type(exc).__name__ if exc else "",
    )
    time.sleep(delay)
