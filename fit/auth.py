"""Google OAuth flow for the Fitness API."""

import os
from pathlib import Path

import config
import paths

SCOPES = [
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.body.read",
]


def credentials_file() -> Path:
    """OAuth client-secrets location, resolved at call time so a file copied
    in via the in-app setup is picked up without restarting."""
    raw = os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else paths.CONFIG_DIR / p
    return paths.FIT_CREDENTIALS_FILE


def _token_file() -> Path:
    return config.DB_PATH.parent / "fit_token.json"


_REFRESH_TIMEOUT_S = 20


def refresh_transport():
    """google-auth HTTP transport with a real timeout applied to every call.

    Setting `Session.timeout` does nothing in requests — the timeout must be
    passed per request, which `google.auth.transport.requests.Request.__call__`
    accepts as a keyword."""
    import functools

    from google.auth.transport.requests import Request

    return functools.partial(Request(), timeout=_REFRESH_TIMEOUT_S)


def _write_token(creds) -> None:
    tf = _token_file()
    try:
        tf.write_text(creds.to_json())
        tf.chmod(0o600)
    except OSError as e:
        raise RuntimeError(
            f"Could not save the Google Fit token at {tf}: {e}\nCheck disk space and file permissions."
        ) from e


def get_credentials():
    """Return valid Google credentials, running the OAuth flow if needed."""
    from google.oauth2.credentials import Credentials

    tf = _token_file()
    creds = None
    if tf.exists():
        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(refresh_transport())
        else:
            creds_file = credentials_file()
            if not creds_file.exists():
                raise FileNotFoundError(
                    f"Google OAuth credentials file not found at '{creds_file}'.\n"
                    "Follow the setup instructions in the menu to create one."
                )
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

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
