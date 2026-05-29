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
        bucket_ms: int = 86_400_000,  # 1 day default
    ) -> dict:
        resp = httpx.post(
            f"{FIT_BASE}/dataset:aggregate",
            headers=self._headers(),
            json={
                "aggregateBy": [{"dataTypeName": dt} for dt in data_types],
                "bucketByTime": {"durationMillis": bucket_ms},
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
