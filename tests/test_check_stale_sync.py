"""Tests for cli._check_stale_sync — startup stale-sync verification."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


def _ts(hours_ago):
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_confirm(answers):
    it = iter(answers)

    def _confirm(msg, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(it, False)
        return m

    return _confirm


def _run(hevy_ts=None, fit_ts=None, fit_connected=False, confirm_answers=None, hevy_client=True,
         fit_sync_raises=None):
    """Execute _check_stale_sync with fully mocked dependencies.

    Always disables auto_sync (patches cli.get_pref → None) so tests that
    exercise the prompt path are not affected by the value stored in the real DB.
    """
    import cli

    incremental_calls = []
    fit_sync_calls = []

    def _get_sync_state(key):
        return {"last_sync": hevy_ts, "fit_last_sync": fit_ts}.get(key)

    def _fake_incremental(client):
        incremental_calls.append(client)
        return {"updated": 3, "deleted": 1}

    def _fake_sync_fit(days):
        if fit_sync_raises:
            raise fit_sync_raises
        fit_sync_calls.append(days)
        return {"daily_days": 90, "sleep_sessions": 5}

    mock_client = MagicMock() if hevy_client else None

    with patch("db.store.get_sync_state", side_effect=_get_sync_state), \
         patch("fit.auth.is_connected", return_value=fit_connected), \
         patch("questionary.confirm", side_effect=_make_confirm(confirm_answers or [])), \
         patch("cli._require_hevy", return_value=mock_client), \
         patch("cli.incremental_sync", side_effect=_fake_incremental), \
         patch("fit.sync.sync_fit", side_effect=_fake_sync_fit), \
         patch("cli.get_pref", return_value=None), \
         patch("cli.console"):
        cli._check_stale_sync()

    return {"incremental_calls": incremental_calls, "fit_sync_calls": fit_sync_calls}


# ── no stale data ─────────────────────────────────────────────────────────────

def test_fresh_hevy_no_prompt():
    import cli
    with patch("db.store.get_sync_state", return_value=_ts(1)), \
         patch("fit.auth.is_connected", return_value=False), \
         patch("cli.get_pref", return_value=None), \
         patch("questionary.confirm") as mock_confirm, \
         patch("cli.console"):
        cli._check_stale_sync()
    mock_confirm.assert_not_called()


def test_hevy_never_synced_no_prompt():
    result = _run(hevy_ts=None, fit_connected=False)
    assert result["incremental_calls"] == []


def test_fit_never_synced_no_prompt():
    result = _run(hevy_ts=_ts(1), fit_ts=None, fit_connected=True)
    assert result["fit_sync_calls"] == []


def test_fit_not_connected_no_prompt():
    result = _run(hevy_ts=_ts(1), fit_ts=_ts(25), fit_connected=False)
    assert result["fit_sync_calls"] == []


# ── hevy stale ────────────────────────────────────────────────────────────────

def test_hevy_stale_prompts_user():
    import cli
    confirm_calls = []

    def _capture_confirm(msg, **kwargs):
        confirm_calls.append(msg)
        m = MagicMock()
        m.ask.return_value = False
        return m

    with patch("db.store.get_sync_state", side_effect=lambda k: _ts(25) if k == "last_sync" else None), \
         patch("fit.auth.is_connected", return_value=False), \
         patch("questionary.confirm", side_effect=_capture_confirm), \
         patch("cli._require_hevy", return_value=None), \
         patch("cli.get_pref", return_value=None), \
         patch("cli.console"):
        cli._check_stale_sync()

    assert any("Hevy" in msg for msg in confirm_calls)


def test_hevy_stale_user_confirms_runs_incremental_sync():
    result = _run(hevy_ts=_ts(25), confirm_answers=[True])
    assert len(result["incremental_calls"]) == 1


def test_hevy_stale_user_declines_no_sync():
    result = _run(hevy_ts=_ts(25), confirm_answers=[False])
    assert result["incremental_calls"] == []


def test_hevy_stale_no_api_key_skips_sync():
    result = _run(hevy_ts=_ts(25), confirm_answers=[True], hevy_client=False)
    assert result["incremental_calls"] == []


def test_hevy_23h_is_not_stale():
    result = _run(hevy_ts=_ts(23), confirm_answers=[True])
    assert result["incremental_calls"] == []


def test_hevy_just_over_24h_is_stale():
    result = _run(hevy_ts=_ts(25), confirm_answers=[True])
    assert len(result["incremental_calls"]) == 1


# ── google fit stale ──────────────────────────────────────────────────────────

def test_fit_stale_prompts_user():
    import cli
    confirm_calls = []

    def _capture_confirm(msg, **kwargs):
        confirm_calls.append(msg)
        m = MagicMock()
        m.ask.return_value = False
        return m

    with patch("db.store.get_sync_state", side_effect=lambda k: _ts(25) if k == "fit_last_sync" else _ts(1)), \
         patch("fit.auth.is_connected", return_value=True), \
         patch("questionary.confirm", side_effect=_capture_confirm), \
         patch("fit.sync.sync_fit"), \
         patch("cli.get_pref", return_value=None), \
         patch("cli.console"):
        cli._check_stale_sync()

    assert any("Google Fit" in msg for msg in confirm_calls)


def test_fit_stale_user_confirms_runs_sync_fit_90_days():
    result = _run(hevy_ts=_ts(1), fit_ts=_ts(25), fit_connected=True, confirm_answers=[True])
    assert result["fit_sync_calls"] == [90]


def test_fit_stale_user_declines_no_sync():
    result = _run(hevy_ts=_ts(1), fit_ts=_ts(25), fit_connected=True, confirm_answers=[False])
    assert result["fit_sync_calls"] == []


def test_fit_sync_exception_does_not_crash():
    result = _run(
        hevy_ts=_ts(1), fit_ts=_ts(25), fit_connected=True,
        confirm_answers=[True], fit_sync_raises=RuntimeError("auth failed"),
    )
    assert result["fit_sync_calls"] == []


# ── both stale ────────────────────────────────────────────────────────────────

def test_both_stale_both_confirmed_runs_both_syncs():
    result = _run(hevy_ts=_ts(25), fit_ts=_ts(25), fit_connected=True, confirm_answers=[True, True])
    assert len(result["incremental_calls"]) == 1
    assert result["fit_sync_calls"] == [90]


def test_both_stale_both_declined_no_syncs():
    result = _run(hevy_ts=_ts(25), fit_ts=_ts(25), fit_connected=True, confirm_answers=[False, False])
    assert result["incremental_calls"] == []
    assert result["fit_sync_calls"] == []


def test_both_stale_only_hevy_confirmed():
    result = _run(hevy_ts=_ts(25), fit_ts=_ts(25), fit_connected=True, confirm_answers=[True, False])
    assert len(result["incremental_calls"]) == 1
    assert result["fit_sync_calls"] == []


def test_both_stale_only_fit_confirmed():
    result = _run(hevy_ts=_ts(25), fit_ts=_ts(25), fit_connected=True, confirm_answers=[False, True])
    assert result["incremental_calls"] == []
    assert result["fit_sync_calls"] == [90]


# ── auto_sync mode ────────────────────────────────────────────────────────────

def test_auto_sync_hevy_syncs_without_prompt():
    """When auto_sync is on, a stale Hevy DB syncs silently — no confirm shown."""
    import cli
    incremental_calls = []

    def _fake_incremental(client):
        incremental_calls.append(client)
        return {"updated": 2, "deleted": 0}

    def _get_pref_auto(key):
        return "1" if key == "auto_sync" else None

    with patch("db.store.get_sync_state", side_effect=lambda k: _ts(25) if k == "last_sync" else None), \
         patch("fit.auth.is_connected", return_value=False), \
         patch("cli.get_pref", side_effect=_get_pref_auto), \
         patch("cli._require_hevy", return_value=MagicMock()), \
         patch("cli.incremental_sync", side_effect=_fake_incremental), \
         patch("questionary.confirm") as mock_confirm, \
         patch("cli.console"):
        cli._check_stale_sync()

    assert len(incremental_calls) == 1
    mock_confirm.assert_not_called()


def test_auto_sync_fit_syncs_without_prompt():
    """When auto_sync is on, a stale Fit DB syncs silently — no confirm shown."""
    import cli
    fit_sync_calls = []

    def _fake_sync_fit(days):
        fit_sync_calls.append(days)
        return {"daily_days": 90, "sleep_sessions": 5}

    def _get_pref_auto(key):
        return "1" if key == "auto_sync" else None

    with patch("db.store.get_sync_state", side_effect=lambda k: _ts(1) if k == "last_sync" else _ts(25)), \
         patch("fit.auth.is_connected", return_value=True), \
         patch("cli.get_pref", side_effect=_get_pref_auto), \
         patch("fit.sync.sync_fit", side_effect=_fake_sync_fit), \
         patch("questionary.confirm") as mock_confirm, \
         patch("cli.console"):
        cli._check_stale_sync()

    assert fit_sync_calls == [30]
    mock_confirm.assert_not_called()


def test_auto_sync_both_stale_syncs_both_silently():
    """When auto_sync is on, both stale sources sync without any prompts."""
    import cli
    incremental_calls = []
    fit_sync_calls = []

    def _fake_incremental(client):
        incremental_calls.append(client)
        return {"updated": 1, "deleted": 0}

    def _fake_sync_fit(days):
        fit_sync_calls.append(days)
        return {"daily_days": 30, "sleep_sessions": 2}

    def _get_pref_auto(key):
        return "1" if key == "auto_sync" else None

    with patch("db.store.get_sync_state", return_value=_ts(25)), \
         patch("fit.auth.is_connected", return_value=True), \
         patch("cli.get_pref", side_effect=_get_pref_auto), \
         patch("cli._require_hevy", return_value=MagicMock()), \
         patch("cli.incremental_sync", side_effect=_fake_incremental), \
         patch("fit.sync.sync_fit", side_effect=_fake_sync_fit), \
         patch("questionary.confirm") as mock_confirm, \
         patch("cli.console"):
        cli._check_stale_sync()

    assert len(incremental_calls) == 1
    assert fit_sync_calls == [30]
    mock_confirm.assert_not_called()


def test_auto_sync_fit_exception_does_not_crash():
    """auto_sync Fit failure is swallowed silently (same as prompt path)."""
    import cli

    def _get_pref_auto(key):
        return "1" if key == "auto_sync" else None

    with patch("db.store.get_sync_state", side_effect=lambda k: _ts(1) if k == "last_sync" else _ts(25)), \
         patch("fit.auth.is_connected", return_value=True), \
         patch("cli.get_pref", side_effect=_get_pref_auto), \
         patch("fit.sync.sync_fit", side_effect=RuntimeError("token expired")), \
         patch("questionary.confirm") as mock_confirm, \
         patch("cli.console"):
        cli._check_stale_sync()   # must not raise

    mock_confirm.assert_not_called()
