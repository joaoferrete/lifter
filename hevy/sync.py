from datetime import datetime, timezone

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from hevy.client import HevyClient
from db.store import (
    init_db,
    upsert_workout,
    delete_workout,
    upsert_exercise_template,
    upsert_body_measurement,
    get_sync_state,
    set_sync_state,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def full_sync(client: HevyClient) -> dict:
    init_db()

    counts = {"workouts": 0, "templates": 0, "body_measurements": 0, "updated_ids": [], "since": None}

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

        task_t = progress.add_task("Syncing exercise templates...", total=None)
        for template in client.get_exercise_templates():
            upsert_exercise_template(template)
            counts["templates"] += 1
            progress.advance(task_t)

        task_b = progress.add_task("Syncing body measurements...", total=None)
        for measurement in client.get_body_measurements():
            upsert_body_measurement(measurement)
            counts["body_measurements"] += 1
            progress.advance(task_b)

    set_sync_state("last_sync", _now_iso())
    return counts


def incremental_sync(client: HevyClient) -> dict:
    last_sync = get_sync_state("last_sync")
    if not last_sync:
        return full_sync(client)

    since = last_sync
    counts = {"updated": 0, "deleted": 0, "templates": 0, "body_measurements": 0, "updated_ids": [], "since": since}

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
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

        task_t = progress.add_task("Refreshing exercise templates...", total=None)
        for template in client.get_exercise_templates():
            upsert_exercise_template(template)
            counts["templates"] += 1
            progress.advance(task_t)

        task_b = progress.add_task("Refreshing body measurements...", total=None)
        for measurement in client.get_body_measurements():
            upsert_body_measurement(measurement)
            counts["body_measurements"] += 1
            progress.advance(task_b)

    set_sync_state("last_sync", _now_iso())
    return counts
