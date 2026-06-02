"""Google Fit REST API client."""
import httpx
from datetime import datetime, timezone

FIT_BASE = "https://www.googleapis.com/fitness/v1/users/me"


class FitClient:
    def __init__(self):
        from fit.auth import get_credentials, disconnect, TOKEN_FILE
        from google.auth.transport.requests import Request

        self._token_file = TOKEN_FILE
        self._disconnect = disconnect
        self._creds = get_credentials()
        self._refresh = Request()

    def _headers(self) -> dict:
        from google.auth.exceptions import RefreshError
        try:
            if not self._creds.valid:
                self._creds.refresh(self._refresh)
        except RefreshError:
            self._disconnect()
            raise RuntimeError(
                "Google Fit token expired and could not be refreshed.\n"
                "Go to Menu → Google Fit → Connect to re-authenticate."
            )
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _check(self, resp: httpx.Response, operation: str) -> None:
        if resp.is_success:
            return
        if resp.status_code == 401:
            self._disconnect()
            raise RuntimeError(
                "Google Fit session expired. Token has been cleared — "
                "go to Menu → Google Fit → Connect to re-authenticate. (error 401)"
            )
        if resp.status_code == 403:
            raise RuntimeError(
                "Google Fit access denied. Make sure you approved all Fitness API scopes "
                "during the OAuth setup. (error 403)"
            )
        if resp.status_code == 400:
            raise RuntimeError(
                f"Google Fit: bad request during {operation}. "
                f"Detail: {resp.text[:300]} (error 400)"
            )
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
            if retry_after:
                raise RuntimeError(
                    f"Google Fit rate limit reached. Try again in {retry_after} seconds. (error 429)"
                )
            raise RuntimeError(
                "Google Fit rate limit reached. Please wait a moment and try again. (error 429)"
            )
        raise RuntimeError(
            f"Google Fit error during {operation}: {resp.text[:200]} (error {resp.status_code})"
        )

    def aggregate(
        self,
        data_types: list[str],
        start_ms: int,
        end_ms: int,
        bucket_ms: int = 86_400_000,
        timezone_id: str | None = None,
    ) -> dict:
        if timezone_id:
            bucket: dict = {"period": {"type": "day", "value": 1, "timeZoneId": timezone_id}}
        else:
            bucket = {"durationMillis": bucket_ms}

        resp = httpx.post(
            f"{FIT_BASE}/dataset:aggregate",
            headers=self._headers(),
            json={
                "aggregateBy": [{"dataTypeName": dt} for dt in data_types],
                "bucketByTime": bucket,
                "startTimeMillis": start_ms,
                "endTimeMillis": end_ms,
            },
            timeout=30,
        )
        self._check(resp, "aggregate")
        return resp.json()

    def get_sleep_sessions(self, start_iso: str, end_iso: str) -> list[dict]:
        sessions: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {"startTime": start_iso, "endTime": end_iso, "activityType": 72}
            if page_token:
                params["pageToken"] = page_token
            resp = httpx.get(
                f"{FIT_BASE}/sessions",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            self._check(resp, "get_sleep_sessions")
            data = resp.json()
            sessions.extend(data.get("session", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return sessions
