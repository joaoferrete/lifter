import httpx
from typing import Iterator
from config import BASE_URL, HEVY_API_KEY


class HevyClient:
    def __init__(self, api_key: str = HEVY_API_KEY):
        self.api_key = api_key
        self._headers = {"api-key": api_key, "Content-Type": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        resp = httpx.get(url, headers=self._headers, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = httpx.post(url, headers=self._headers, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, body: dict) -> dict:
        url = f"{BASE_URL}{path}"
        resp = httpx.put(url, headers=self._headers, json=body, timeout=30)
        resp.raise_for_status()
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
        return self._post("/v1/routines", {"routine": _sanitize_routine(routine)})

    def update_routine(self, routine_id: str, routine: dict) -> dict:
        return self._put(f"/v1/routines/{routine_id}", {"routine": _sanitize_routine(routine)})

    # --- Exercise templates ---

    def get_exercise_templates(self) -> Iterator[dict]:
        return self._paginate("/v1/exercise_templates", "exercise_templates", page_size=100)

    def get_exercise_template(self, template_id: str) -> dict:
        return self._get(f"/v1/exercise_templates/{template_id}")["exercise_template"]

    def create_exercise_template(self, exercise: dict) -> dict:
        return self._post("/v1/exercise_templates", {"exercise": exercise})

    # --- Exercise history ---

    def get_exercise_history(self, template_id: str) -> Iterator[dict]:
        return self._paginate(f"/v1/exercise_history/{template_id}", "history", page_size=10)

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
        return self._put(f"/v1/body_measurements/{date}", measurement)


def _sanitize_routine(routine: dict) -> dict:
    """Strip fields the Hevy API doesn't accept in POST/PUT /v1/routines."""
    def clean_set(s: dict) -> dict:
        return {k: v for k, v in {
            "type":             s.get("type", "normal"),
            "weight_kg":        s.get("weight_kg"),
            "reps":             s.get("reps"),
            "distance_meters":  s.get("distance_meters"),
            "duration_seconds": s.get("duration_seconds"),
            "custom_metric":    s.get("custom_metric"),
            "rep_range":        s.get("rep_range"),
        }.items() if v is not None}

    def clean_exercise(ex: dict) -> dict:
        return {k: v for k, v in {
            "exercise_template_id": ex["exercise_template_id"],
            "superset_id":          ex.get("superset_id"),
            "rest_seconds":         ex.get("rest_seconds"),
            "notes":                ex.get("notes"),
            "sets":                 [clean_set(s) for s in ex.get("sets", [])],
        }.items() if v is not None}

    return {k: v for k, v in {
        "title":     routine.get("title", ""),
        "folder_id": routine.get("folder_id"),
        "notes":     routine.get("notes"),
        "exercises": [clean_exercise(ex) for ex in routine.get("exercises", [])],
    }.items() if v is not None}
