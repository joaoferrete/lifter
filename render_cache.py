"""Tiny in-process cache for derived render data (goal progress, set tallies).

These values are recomputed often during menu rendering but the underlying data
only changes on sync or an explicit edit — so we memoize by key and invalidate
explicitly instead of expiring by time.

Entries are namespaced by the active database path, so switching profiles (which
swaps ``config.DB_PATH``) and the per-test isolated databases never read each
other's cached values.
"""

_store: dict = {}


def _ns() -> str:
    import config
    return str(config.DB_PATH)


def cached(key: str, producer):
    """Return the cached value for ``key``, computing it via ``producer()`` on a miss."""
    full = (_ns(), key)
    if full not in _store:
        _store[full] = producer()
    return _store[full]


def invalidate() -> None:
    """Drop all cached derived data — call after sync or any data mutation."""
    _store.clear()
