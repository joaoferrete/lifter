import httpx
from typing import Iterator
from config import BASE_URL, HEVY_API_KEY

_VALID_SET_TYPES = {"warmup", "normal", "failure", "dropset"}


class HevyClient:
    def __init__(self, api_key: str = HEVY_API_KEY):
        self.api_key = api_key
        self._headers = {"api-key": api_key, "Content-Type": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        resp = httpx.get(url, headers=self._headers, params=params or {}, timeout=30)
        _raise(resp, path)
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = httpx.post(url, headers=self._headers, json=body, timeout=30)
        _raise(resp, path)
        return resp.json()

    def _put(self, path: str, body: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = httpx.put(url, headers=self._headers, json=body, timeout=30)
        _raise(resp, path)
        return resp.json()

    def _paginate(self, path: str, key: str, page_size: int = 10, **params) -> Iterator[dict]:
        page = 1
        while True:
            data = self._get(path, {"page": page, "pageSize": page_size, **params})
            yield from data.get(key, [])
            if page >= data.get("page_count", 1):
                break
            page += 1

    # --- Workouts ---

    def get_workouts(self, page_size: int = 10) -> Iterator[dict]:
        return self._paginate("/v1/workouts", "workouts", page_size=page_size)

    def get_workout_count(self) -> int:
        return self._get("/v1/workouts/count")["workout_count"]

    def get_workout(self, workout_id: str) -> dict:
        return self._get(f"/v1/workouts/{workout_id}")["workout"]

    def get_workout_events(self, since: str, page_size: int = 10) -> Iterator[dict]:
        return self._paginate("/v1/workouts/events", "events", page_size=page_size, since=since)

    def create_workout(self, workout: dict) -> dict:
        return self._post("/v1/workouts", {"workout": workout})

    def update_workout(self, workout_id: str, workout: dict) -> dict:
        return self._put(f"/v1/workouts/{workout_id}", {"workout": workout})

    # --- User ---

    def get_user_info(self) -> dict:
        return self._get("/v1/user/info")["data"]

    # --- Routines ---

    def get_routines(self) -> Iterator[dict]:
        return self._paginate("/v1/routines", "routines", page_size=10)

    def get_routine(self, routine_id: str) -> dict:
        return self._get(f"/v1/routines/{routine_id}")["routine"]

    def create_routine(self, routine: dict) -> dict:
        return self._post("/v1/routines", {"routine": _sanitize_routine(routine, for_put=False)})

    def update_routine(self, routine_id: str, routine: dict) -> dict:
        return self._put(f"/v1/routines/{routine_id}", {"routine": _sanitize_routine(routine, for_put=True)})

    # --- Exercise templates ---

    def get_exercise_templates(self) -> Iterator[dict]:
        return self._paginate("/v1/exercise_templates", "exercise_templates", page_size=100)

    def get_exercise_template(self, template_id: str) -> dict:
        return self._get(f"/v1/exercise_templates/{template_id}")["exercise_template"]

    def create_exercise_template(self, exercise: dict) -> dict:
        return self._post("/v1/exercise_templates", {"exercise": exercise})

    # --- Exercise history ---

    def get_exercise_history(self, template_id: str) -> list[dict]:
        # This endpoint is not paginated — returns all history in one response.
        data = self._get(f"/v1/exercise_history/{template_id}")
        return data.get("exercise_history", data.get("history", []))

    # --- Routine folders ---

    def get_routine_folders(self) -> Iterator[dict]:
        return self._paginate("/v1/routine_folders", "routine_folders", page_size=10)

    def create_routine_folder(self, title: str) -> dict:
        return self._post("/v1/routine_folders", {"routine_folder": {"title": title}})

    # --- Body measurements ---

    def get_body_measurements(self) -> Iterator[dict]:
        return self._paginate("/v1/body_measurements", "body_measurements", page_size=10)

    def get_body_measurement(self, date: str) -> dict:
        return self._get(f"/v1/body_measurements/{date}")["body_measurement"]

    def create_body_measurement(self, measurement: dict) -> dict:
        return self._post("/v1/body_measurements", {"body_measurement": measurement})

    def update_body_measurement(self, date: str, measurement: dict) -> dict:
        # Wrap to match POST pattern (spec body is undefined but consistency matters).
        return self._put(f"/v1/body_measurements/{date}", {"body_measurement": measurement})


# ── helpers ───────────────────────────────────────────────────────────────────

def _routine_id(resp) -> str:
    """Extract routine id from any Hevy response shape (dict, wrapped dict, or list)."""
    if isinstance(resp, list):
        resp = resp[0] if resp else {}
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("routine") or resp
    if isinstance(inner, dict):
        return str(inner.get("id", ""))
    return ""


def _raise(resp: httpx.Response, path: str) -> None:
    """Raise with the API error body included so debugging is easier."""
    if resp.is_success:
        return
    try:
        detail = resp.json()
    except Exception:
        detail = resp.text[:400]

    code = resp.status_code
    if code == 429:
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if retry_after:
            msg = f"Hevy API rate limit reached. Try again in {retry_after} seconds. (error 429)"
        else:
            msg = "Hevy API rate limit reached. Please wait a moment and try again. (error 429)"
    elif code == 401:
        msg = "Hevy API key is invalid or expired. Check your HEVY_API_KEY in .env. (error 401)"
    elif code == 403:
        msg = "Access denied to Hevy. Check your API key permissions. (error 403)"
    elif code == 404:
        msg = f"Hevy resource not found at {path}. (error 404)"
    elif code == 422:
        msg = f"Hevy rejected the request data at {path}: {detail} (error 422)"
    elif code >= 500:
        msg = f"Hevy server error. Try again in a moment. (error {code})"
    else:
        msg = f"Hevy API error at {path}: {detail} (error {code})"

    raise RuntimeError(msg)


def _sanitize_routine(routine: dict, *, for_put: bool = False) -> dict:
    """
    Produce a payload that matches PostRoutinesRequestBody / PutRoutinesRequestBody.

    Key rules enforced here:
    - reps and rest_seconds must be integers (Gemini returns floats).
    - weight_kg must be float or None.
    - set type must be one of the four valid enum values.
    - exercises without a valid exercise_template_id are dropped.
    - empty-string fields are treated as absent.
    - folder_id is only included for POST (not in PutRoutinesRequestBody).
    """
    def _int(v) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _float(v) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _str(v) -> str | None:
        s = str(v).strip() if v is not None else None
        return s if s else None

    def clean_set(s) -> dict | None:
        # Gemini sometimes returns sets as [[{...}]] instead of [{...}]
        if isinstance(s, list):
            s = s[0] if s and isinstance(s[0], dict) else None
        if not isinstance(s, dict):
            return None
        set_type = s.get("type", "normal")
        if set_type not in _VALID_SET_TYPES:
            set_type = "normal"
        result = {"type": set_type}
        if s.get("weight_kg") is not None:
            result["weight_kg"] = _float(s["weight_kg"])
        if s.get("reps") is not None:
            result["reps"] = _int(s["reps"])
        if s.get("distance_meters") is not None:
            result["distance_meters"] = _int(s["distance_meters"])
        if s.get("duration_seconds") is not None:
            result["duration_seconds"] = _int(s["duration_seconds"])
        if s.get("custom_metric") is not None:
            result["custom_metric"] = _float(s["custom_metric"])
        if s.get("rep_range") is not None:
            result["rep_range"] = s["rep_range"]
        return result

    def clean_exercise(ex) -> dict | None:
        # Gemini sometimes returns exercises as [[{...}]] instead of [{...}]
        if isinstance(ex, list):
            ex = ex[0] if ex and isinstance(ex[0], dict) else None
        if not isinstance(ex, dict):
            return None
        tid = _str(ex.get("exercise_template_id"))
        if not tid:
            return None
        sets = [cs for s in ex.get("sets", []) if (cs := clean_set(s)) is not None]
        result: dict = {"exercise_template_id": tid, "sets": sets}
        rest = _int(ex.get("rest_seconds"))
        if rest is not None:
            result["rest_seconds"] = rest
        notes = _str(ex.get("notes"))
        if notes:
            result["notes"] = notes
        sid = ex.get("superset_id")
        if sid is not None:
            result["superset_id"] = _int(sid)
        return result

    exercises = [e for ex in routine.get("exercises", []) if (e := clean_exercise(ex))]
    title = _str(routine.get("title")) or "New Routine"
    notes = _str(routine.get("notes"))

    payload: dict = {"title": title, "exercises": exercises}
    if notes:
        payload["notes"] = notes
    if not for_put:
        folder_id = routine.get("folder_id")
        # The Hevy API requires folder_id to be explicitly null (not absent)
        # to route the routine to the default "My Routines" folder. When the
        # field is missing entirely, the Node.js server reads undefined and
        # returns "Invalid routine folder id: undefined".
        payload["folder_id"] = int(folder_id) if isinstance(folder_id, int) else None

    return payload
