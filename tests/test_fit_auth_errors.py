"""Tests for fit/auth.py — error classification, client-secrets inspection,
and the refresh-failure fallthrough to the browser flow."""

import json
from unittest.mock import MagicMock

import pytest

# ── classify_auth_error ───────────────────────────────────────────────────────


def test_classify_access_denied_real_exception():
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError

    from fit.auth import classify_auth_error

    assert classify_auth_error(AccessDeniedError()) == "error.fit_access_denied"


def test_classify_access_denied_by_message():
    from fit.auth import classify_auth_error

    assert classify_auth_error(Exception("(access_denied) blocked")) == "error.fit_access_denied"


def test_classify_flow_timeout():
    from google_auth_oauthlib.flow import WSGITimeoutError

    from fit.auth import classify_auth_error

    assert classify_auth_error(WSGITimeoutError("timed out")) == "error.fit_auth_timeout"


def test_classify_web_client_secrets_valueerror():
    from fit.auth import classify_auth_error

    e = ValueError("Client secrets must be for a web or installed app.")
    assert classify_auth_error(e) == "error.fit_web_client"


def test_classify_redirect_uri_mismatch():
    from fit.auth import classify_auth_error

    assert classify_auth_error(Exception("Error: redirect_uri_mismatch")) == "error.fit_web_client"


def test_classify_refresh_error():
    from google.auth.exceptions import RefreshError

    from fit.auth import classify_auth_error

    assert classify_auth_error(RefreshError("invalid_grant: Token has been expired")) == (
        "error.fit_token_refresh_expired"
    )


def test_classify_unknown_returns_none():
    from fit.auth import classify_auth_error

    assert classify_auth_error(KeyError("x")) is None


# ── describe_client ───────────────────────────────────────────────────────────


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_describe_client_installed(tmp_path):
    from fit.auth import describe_client

    p = _write_json(
        tmp_path / "c.json",
        {"installed": {"client_id": "abc.apps.googleusercontent.com", "project_id": "my-proj"}},
    )
    assert describe_client(p) == {
        "client_id": "abc.apps.googleusercontent.com",
        "project_id": "my-proj",
        "type": "installed",
    }


def test_describe_client_web(tmp_path):
    from fit.auth import describe_client

    p = _write_json(tmp_path / "c.json", {"web": {"client_id": "web-id", "project_id": "p"}})
    assert describe_client(p)["type"] == "web"


def test_describe_client_garbage_returns_none(tmp_path):
    from fit.auth import describe_client

    p = tmp_path / "c.json"
    p.write_text("not json")
    assert describe_client(p) is None


def test_describe_client_missing_file_returns_none(tmp_path):
    from fit.auth import describe_client

    assert describe_client(tmp_path / "nope.json") is None


def test_describe_client_wrong_shape_returns_none(tmp_path):
    from fit.auth import describe_client

    p = _write_json(tmp_path / "c.json", {"other": {}})
    assert describe_client(p) is None


# ── get_credentials refresh fallthrough ───────────────────────────────────────


def test_get_credentials_reruns_flow_when_refresh_fails(monkeypatch, tmp_path):
    """A RefreshError (7-day Testing expiry) must drop the token and re-run the
    browser flow instead of bubbling invalid_grant to the caller."""
    from google.auth.exceptions import RefreshError

    import config
    import fit.auth as auth

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hevy.db")
    token_file = tmp_path / "fit_token.json"
    token_file.write_text("{}")

    stale = MagicMock()
    stale.valid = False
    stale.expired = True
    stale.refresh_token = "stale-token"
    stale.refresh.side_effect = RefreshError("invalid_grant")
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        staticmethod(lambda *a, **k: stale),
    )

    fresh = MagicMock()
    fresh.to_json.return_value = '{"token": "fresh"}'
    monkeypatch.setattr(auth, "_run_browser_flow", lambda: fresh)

    creds = auth.get_credentials()

    assert creds is fresh
    assert json.loads(token_file.read_text()) == {"token": "fresh"}


def test_get_credentials_raises_when_no_credentials_file(monkeypatch, tmp_path):
    import config
    import fit.auth as auth
    import paths

    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hevy.db")
    monkeypatch.setattr(paths, "FIT_CREDENTIALS_FILE", tmp_path / "absent.json")

    with pytest.raises(FileNotFoundError):
        auth.get_credentials()
