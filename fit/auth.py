"""Google OAuth flow for the Fitness API."""
import os
import stat
from pathlib import Path

import config

SCOPES = [
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.body.read",
]

CREDENTIALS_FILE = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", Path(__file__).resolve().parent.parent / "fit_credentials.json"))


def _token_file() -> Path:
    return config.DB_PATH.parent / "fit_token.json"


def _write_token(creds) -> None:
    tf = _token_file()
    tf.write_text(creds.to_json())
    tf.chmod(0o600)


def get_credentials():
    """Return valid Google credentials, running the OAuth flow if needed."""
    import requests as _requests
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    tf = _token_file()
    creds = None
    if tf.exists():
        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            _session = _requests.Session()
            _session.timeout = 20
            creds.refresh(Request(session=_session))
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Google OAuth credentials file not found at '{CREDENTIALS_FILE}'.\n"
                    "Follow the setup instructions in the menu to create one."
                )
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
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
