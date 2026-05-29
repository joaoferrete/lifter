"""Google Fit REST API client."""
import httpx
from datetime import datetime, timezone

FIT_BASE = "https://www.googleapis.com/fitness/v1/users/me"


class FitClient:
    def __init__(self):
        from fit.auth import get_credentials
        from google.auth.transport.requests import Request

        self._creds = get_credentials()
        self._refresh = Request()

    def _headers(self) -> dict:
        if not self._creds.valid:
            self._creds.refresh(self._refresh)
        return {"Authorization": f"Bearer {self._creds.token}"}

    def aggregate(
        self,
        data_types: list[str],
        start_ms: int,
        end_ms: int,
        bucket_ms: int = 86_400_000,
        timezone_id: str | None = None,
    ) -> dict:
        bucket: dict = {"durationMillis": bucket_ms}
        if timezone_id:
            # Period-based bucketing aligns to local midnight instead of UTC midnight,
            # which fixes steps being assigned to the wrong day for non-UTC users.
            bucket = {"period": {"type": "day", "value": 1, "timeZoneId": timezone_id}}

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
        resp.raise_for_status()
        return resp.json()

    def get_sleep_sessions(self, start_iso: str, end_iso: str) -> list[dict]:
        resp = httpx.get(
            f"{FIT_BASE}/sessions",
            headers=self._headers(),
            params={"startTime": start_iso, "endTime": end_iso, "activityType": 72},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("session", [])
