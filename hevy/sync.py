from datetime import UTC, datetime
from typing import Any

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from db.store import (
    delete_stale_routines,
    delete_workout,
    get_sync_state,
    init_db,
    record_sync_result,
    set_sync_state,
    upsert_body_measurement,
    upsert_exercise_template,
    upsert_routine,
    upsert_workout,
)
from hevy.client import HevyClient


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sync_routines(client: HevyClient, progress: Progress, counts: dict) -> None:
    """Fetch all routines from Hevy and upsert locally, removing deleted ones."""
    task_r = progress.add_task("Syncing routines...", total=None)
    fetched_ids: set[str] = set()
    for routine in client.get_routines():
        upsert_routine(routine)
        fetched_ids.add(str(routine["id"]))
        counts["routines"] = counts.get("routines", 0) + 1
        progress.advance(task_r)

    delete_stale_routines(fetched_ids)


def _sync_shared_entities(client: HevyClient, progress: Progress, counts: dict, verb: str) -> None:
    """Templates, body measurements and routines — identical in both sync modes."""
    task_t = progress.add_task(f"{verb} exercise templates...", total=None)
    for template in client.get_exercise_templates():
        upsert_exercise_template(template)
        counts["templates"] += 1
        progress.advance(task_t)

    task_b = progress.add_task(f"{verb} body measurements...", total=None)
    for measurement in client.get_body_measurements():
        upsert_body_measurement(measurement)
        counts["body_measurements"] += 1
        progress.advance(task_b)

    _sync_routines(client, progress, counts)


def _finish_sync(detail: str) -> None:
    """Common success tail: stamp the sync, record the result, drop caches."""
    set_sync_state("last_sync", _now_iso())
    record_sync_result("last_sync_result", True, detail)
    from render_cache import invalidate

    invalidate()


def full_sync(client: HevyClient) -> dict:
    from debug_log import log

    log("SYNC", "Hevy full sync started")
    init_db()

    counts: dict[str, Any] = {
        "workouts": 0,
        "templates": 0,
        "body_measurements": 0,
        "routines": 0,
        "updated_ids": [],
        "since": None,
    }

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True,
        ) as progress:
            total_workouts = client.get_workout_count()

            task_w = progress.add_task("Syncing workouts...", total=total_workouts)
            for workout in client.get_workouts(page_size=10):
                upsert_workout(workout)
                counts["workouts"] += 1
                progress.advance(task_w)

            _sync_shared_entities(client, progress, counts, "Syncing")
    except Exception as e:
        record_sync_result("last_sync_result", False, f"{type(e).__name__}: {e}")
        raise

    _finish_sync(f"full: {counts['workouts']} workouts")
    log(
        "SYNC",
        "Hevy full sync complete",
        workouts=counts["workouts"],
        templates=counts["templates"],
        body_measurements=counts["body_measurements"],
        routines=counts["routines"],
    )
    return counts


def incremental_sync(client: HevyClient) -> dict:
    from debug_log import log

    last_sync = get_sync_state("last_sync")
    if not last_sync:
        return full_sync(client)

    since = last_sync
    log("SYNC", "Hevy incremental sync started", since=since)
    counts: dict[str, Any] = {
        "updated": 0,
        "deleted": 0,
        "templates": 0,
        "body_measurements": 0,
        "routines": 0,
        "updated_ids": [],
        "since": since,
    }

    try:
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True
        ) as progress:
            task = progress.add_task(f"Fetching events since {last_sync}...", total=None)
            for event in client.get_workout_events(since=last_sync):
                if event.get("type") == "updated":
                    upsert_workout(event["workout"])
                    counts["updated"] += 1
                    counts["updated_ids"].append(event["workout"]["id"])
                elif event.get("type") == "deleted":
                    delete_workout(event["id"])
                    counts["deleted"] += 1
                progress.advance(task)

            _sync_shared_entities(client, progress, counts, "Refreshing")
    except Exception as e:
        record_sync_result("last_sync_result", False, f"{type(e).__name__}: {e}")
        raise

    _finish_sync(f"incremental: {counts['updated']} updated · {counts['deleted']} deleted")
    log(
        "SYNC",
        "Hevy incremental sync complete",
        updated=counts["updated"],
        deleted=counts["deleted"],
        routines=counts["routines"],
    )
    return counts
