"""Google OAuth flow for the Fitness API."""

import json
import os
from pathlib import Path
from typing import Any

import config
import paths

SCOPES = [
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.body.read",
]


def profile_credentials_file() -> Path:
    """Optional per-profile client-secrets file, next to the profile's DB."""
    return config.DB_PATH.parent / "fit_credentials.json"


def credentials_file() -> Path:
    """OAuth client-secrets location, resolved at call time so a file copied
    in via the in-app setup is picked up without restarting.

    Resolution order: GOOGLE_CREDENTIALS_FILE env → per-profile file →
    global file shared by all profiles."""
    raw = os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else paths.CONFIG_DIR / p
    pcf = profile_credentials_file()
    if pcf.exists():
        return pcf
    return paths.FIT_CREDENTIALS_FILE


def describe_client(path: Path) -> dict | None:
    """{'client_id', 'project_id', 'type'} from a client-secrets JSON, or None."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        kind = "installed" if "installed" in payload else "web" if "web" in payload else None
        if kind is None:
            return None
        info = payload.get(kind) or {}
        return {
            "client_id": info.get("client_id", "?"),
            "project_id": info.get("project_id", "?"),
            "type": kind,
        }
    except Exception:
        return None


def classify_auth_error(e: BaseException) -> str | None:
    """Map an OAuth-flow exception to an i18n error key, or None if unknown.

    Matches by class name (not isinstance) so tests and callers don't need
    oauthlib/google-auth imported."""
    msg = str(e).lower()
    names = {c.__name__ for c in type(e).__mro__}
    if "AccessDeniedError" in names or "access_denied" in msg:
        return "error.fit_access_denied"
    if "WSGITimeoutError" in names or isinstance(e, AttributeError):
        return "error.fit_auth_timeout"
    if "redirect_uri_mismatch" in msg or (isinstance(e, ValueError) and "client secrets" in msg):
        return "error.fit_web_client"
    if "invalid_grant" in msg or "RefreshError" in names:
        return "error.fit_token_refresh_expired"
    return None


def _token_file() -> Path:
    return config.DB_PATH.parent / "fit_token.json"


_REFRESH_TIMEOUT_S = 20
_FLOW_TIMEOUT_S = 300


def refresh_transport() -> Any:
    """google-auth HTTP transport with a real timeout applied to every call.

    Setting `Session.timeout` does nothing in requests — the timeout must be
    passed per request, which `google.auth.transport.requests.Request.__call__`
    accepts as a keyword."""
    import functools

    from google.auth.transport.requests import Request

    return functools.partial(Request(), timeout=_REFRESH_TIMEOUT_S)


def _write_token(creds: Any) -> None:
    tf = _token_file()
    try:
        tf.write_text(creds.to_json())
        tf.chmod(0o600)
    except OSError as e:
        raise RuntimeError(
            f"Could not save the Google Fit token at {tf}: {e}\nCheck disk space and file permissions."
        ) from e


def _run_browser_flow() -> Any:
    creds_file = credentials_file()
    if not creds_file.exists():
        raise FileNotFoundError(
            f"Google OAuth credentials file not found at '{creds_file}'.\n"
            "Follow the setup instructions in the menu to create one."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    return flow.run_local_server(port=0, open_browser=True, timeout_seconds=_FLOW_TIMEOUT_S)


def get_credentials() -> Any:
    """Return valid Google credentials, running the OAuth flow if needed."""
    from google.oauth2.credentials import Credentials

    tf = _token_file()
    creds = None
    if tf.exists():
        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.exceptions import RefreshError

            try:
                creds.refresh(refresh_transport())
            except RefreshError:
                # Testing-mode projects expire refresh tokens after ~7 days;
                # drop the stale token and re-run the full browser flow.
                disconnect()
                creds = _run_browser_flow()
        else:
            creds = _run_browser_flow()

        _write_token(creds)

    return creds


def is_connected() -> bool:
    """True only if the token file exists AND contains a usable credential."""
    tf = _token_file()
    if not tf.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)
        return creds is not None and (creds.valid or bool(creds.refresh_token))
    except Exception:
        return False


def disconnect() -> None:
    tf = _token_file()
    if tf.exists():
        tf.unlink()
