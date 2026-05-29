"""Google OAuth flow for the Fitness API."""
import os
from pathlib import Path

from config import DB_PATH

SCOPES = [
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.body.read",
]

# Credentials file downloaded from Google Cloud Console
CREDENTIALS_FILE = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", "fit_credentials.json"))

# Token stored next to the DB
TOKEN_FILE = DB_PATH.parent / "fit_token.json"


def get_credentials():
    """Return valid Google credentials, running the OAuth flow if needed."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Google OAuth credentials file not found at '{CREDENTIALS_FILE}'.\n"
                    "Follow the setup instructions in the menu to create one."
                )
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

        TOKEN_FILE.write_text(creds.to_json())

    return creds


def is_connected() -> bool:
    return TOKEN_FILE.exists()


def disconnect() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
