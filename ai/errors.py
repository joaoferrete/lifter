"""User-friendly error mapping for AI provider exceptions."""

import re

from i18n import _


def friendly_error(e: Exception) -> str:
    """Return a user-friendly, localized message for an AI provider exception."""
    try:
        import config as _cfg
        import debug_log

        _status = getattr(e, "status_code", None) or getattr(e, "code", None)
        debug_log.error(
            "AI",
            f"{type(e).__name__}: {str(e)[:200]}",
            exc=e,
            provider=_cfg.AI_PROVIDER,
            model=_cfg.AI_MODEL,
            status=_status,
        )
    except Exception:
        pass  # logging must never mask the original error

    msg = str(e)
    status = getattr(e, "status_code", None) or getattr(e, "code", None)

    # Try to extract status from common exception attribute patterns
    if status is None:
        for attr in ("response", "_response"):
            resp = getattr(e, attr, None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
                break

    if status == 429:
        retry_after = None
        for attr in ("response", "_response"):
            resp = getattr(e, attr, None)
            if resp is not None:
                headers = getattr(resp, "headers", {}) or {}
                retry_after = headers.get("Retry-After") or headers.get("retry-after")
                break
        if not retry_after and "retry after" in msg.lower():
            m = re.search(r"(\d+)\s*s", msg, re.IGNORECASE)
            retry_after = m.group(1) if m else None
        if retry_after:
            return _("error.rate_limit_429", retry_after=retry_after)
        return _("error.rate_limit_429_no_retry")
    if status == 413 or "rate_limit_exceeded" in msg or "tokens per minute" in msg.lower():
        return _("error.request_too_large_413")
    if status == 401:
        return _("error.api_key_invalid_401")
    if status == 403:
        return _("error.access_denied_403")
    if status == 400:
        return _("error.bad_request_400")
    if status is not None and status >= 500:
        return _("error.server_error_5xx", status=status)
    if status is not None:
        return _("error.generic_status", status=status)
    return _("error.generic", exc_type=type(e).__name__)


# Historical name — several call sites and tests still use the underscore form.
_friendly_error = friendly_error
