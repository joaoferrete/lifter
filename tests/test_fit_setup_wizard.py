"""Tests for commands.fit._fit_setup — the Connect wizard flow."""

import json
from unittest.mock import MagicMock

import pytest


def _answer(value):
    """A questionary.* stand-in whose .ask() returns `value`."""

    def _factory(*args, **kwargs):
        m = MagicMock()
        m.ask.return_value = value
        return m

    return _factory


@pytest.fixture
def wizard_env(monkeypatch, tmp_path):
    """Sandbox global + per-profile credential locations for the wizard."""
    import config
    import paths

    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    monkeypatch.setattr(config, "DB_PATH", profile_dir / "hevy.db")
    global_creds = tmp_path / "global" / "fit_credentials.json"
    global_creds.parent.mkdir()
    monkeypatch.setattr(paths, "FIT_CREDENTIALS_FILE", global_creds)
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    return {"global": global_creds, "profile": profile_dir / "fit_credentials.json", "tmp": tmp_path}


def _run_setup():
    from commands.fit import _fit_setup
    from ui.console import console

    with console.capture() as cap:
        _fit_setup()
    return cap.get()


INSTALLED_JSON = {"installed": {"client_id": "abc123.apps.googleusercontent.com", "project_id": "lifter-proj"}}
WEB_JSON = {"web": {"client_id": "web-id", "project_id": "web-proj"}}


def test_first_setup_rejects_web_client_json(monkeypatch, wizard_env, tmp_path):
    source = tmp_path / "downloaded.json"
    source.write_text(json.dumps(WEB_JSON))
    monkeypatch.setattr("questionary.path", _answer(str(source)))

    called = []
    monkeypatch.setattr("fit.auth.get_credentials", lambda: called.append(1))

    out = _run_setup()

    assert "Desktop app" in out
    assert not wizard_env["global"].exists()
    assert not called


def test_first_setup_saves_valid_json_to_global(monkeypatch, wizard_env, tmp_path):
    source = tmp_path / "downloaded.json"
    source.write_text(json.dumps(INSTALLED_JSON))
    monkeypatch.setattr("questionary.path", _answer(str(source)))
    monkeypatch.setattr("questionary.confirm", _answer(False))  # stop before auth

    out = _run_setup()

    assert wizard_env["global"].exists()
    assert (wizard_env["global"].stat().st_mode & 0o777) == 0o600
    assert json.loads(wizard_env["global"].read_text()) == INSTALLED_JSON
    assert "Test user" in out  # pre-auth reminder shown


def test_existing_client_shows_reuse_menu_with_client_id(monkeypatch, wizard_env):
    wizard_env["global"].write_text(json.dumps(INSTALLED_JSON))
    monkeypatch.setattr("questionary.select", _answer("cancel"))

    out = _run_setup()

    assert "abc123.apps.googleusercontent.com" in out
    assert "lifter-proj" in out


def test_existing_client_new_json_goes_to_profile_file(monkeypatch, wizard_env, tmp_path):
    wizard_env["global"].write_text(json.dumps(INSTALLED_JSON))
    other = {"installed": {"client_id": "other-id", "project_id": "other-proj"}}
    source = tmp_path / "other.json"
    source.write_text(json.dumps(other))

    monkeypatch.setattr("questionary.select", _answer("new"))
    monkeypatch.setattr("questionary.path", _answer(str(source)))
    monkeypatch.setattr("questionary.confirm", _answer(False))

    _run_setup()

    assert json.loads(wizard_env["profile"].read_text()) == other
    # global file untouched
    assert json.loads(wizard_env["global"].read_text()) == INSTALLED_JSON


def test_existing_web_client_is_replaced_in_place(monkeypatch, wizard_env, tmp_path):
    wizard_env["global"].write_text(json.dumps(WEB_JSON))
    source = tmp_path / "fixed.json"
    source.write_text(json.dumps(INSTALLED_JSON))
    monkeypatch.setattr("questionary.path", _answer(str(source)))
    monkeypatch.setattr("questionary.confirm", _answer(False))

    out = _run_setup()

    assert "Desktop app" in out
    assert json.loads(wizard_env["global"].read_text()) == INSTALLED_JSON


def test_access_denied_maps_to_actionable_message(monkeypatch, wizard_env):
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError

    wizard_env["global"].write_text(json.dumps(INSTALLED_JSON))
    monkeypatch.setattr("questionary.select", _answer("existing"))
    monkeypatch.setattr("questionary.confirm", _answer(True))

    def _boom():
        raise AccessDeniedError()

    monkeypatch.setattr("fit.auth.get_credentials", _boom)

    out = _run_setup()

    assert "access_denied" in out
    assert "Test user" in out


def test_flow_timeout_maps_to_timeout_message(monkeypatch, wizard_env):
    from google_auth_oauthlib.flow import WSGITimeoutError

    wizard_env["global"].write_text(json.dumps(INSTALLED_JSON))
    monkeypatch.setattr("questionary.select", _answer("existing"))
    monkeypatch.setattr("questionary.confirm", _answer(True))

    def _boom():
        raise WSGITimeoutError("timed out")

    monkeypatch.setattr("fit.auth.get_credentials", _boom)

    out = _run_setup()

    assert "5 minutes" in out


def test_unknown_error_shows_generic_with_detail(monkeypatch, wizard_env):
    wizard_env["global"].write_text(json.dumps(INSTALLED_JSON))
    monkeypatch.setattr("questionary.select", _answer("existing"))
    monkeypatch.setattr("questionary.confirm", _answer(True))

    def _boom():
        raise KeyError("weird")

    monkeypatch.setattr("fit.auth.get_credentials", _boom)

    out = _run_setup()

    assert "KeyError" in out
