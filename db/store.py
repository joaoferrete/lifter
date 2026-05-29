import json
import sqlite3
from pathlib import Path
from typing import Any

from config import DB_PATH


def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with _conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workouts (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                description TEXT,
                routine_id  TEXT,
                start_time  TEXT,
                end_time    TEXT,
                updated_at  TEXT,
                created_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS workout_exercises (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id           TEXT NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                exercise_template_id TEXT NOT NULL,
                title                TEXT,
                notes                TEXT,
                idx                  INTEGER,
                superset_id          INTEGER
            );

            CREATE TABLE IF NOT EXISTS workout_sets (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_exercise_id  INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
                workout_id           TEXT NOT NULL,
                exercise_template_id TEXT NOT NULL,
                idx                  INTEGER,
                type                 TEXT,
                weight_kg            REAL,
                reps                 INTEGER,
                distance_meters      INTEGER,
                duration_seconds     INTEGER,
                rpe                  REAL,
                custom_metric        REAL
            );

            CREATE TABLE IF NOT EXISTS exercise_templates (
                id                      TEXT PRIMARY KEY,
                title                   TEXT,
                type                    TEXT,
                primary_muscle_group    TEXT,
                secondary_muscle_groups TEXT,
                is_custom               INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS body_measurements (
                date           TEXT PRIMARY KEY,
                weight_kg      REAL,
                lean_mass_kg   REAL,
                fat_percent    REAL,
                neck_cm        REAL,
                shoulder_cm    REAL,
                chest_cm       REAL,
                left_bicep_cm  REAL,
                right_bicep_cm REAL,
                left_forearm_cm  REAL,
                right_forearm_cm REAL,
                abdomen        REAL,
                waist          REAL,
                hips           REAL,
                left_thigh     REAL,
                right_thigh    REAL,
                left_calf      REAL,
                right_calf     REAL
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS fit_sleep (
                date           TEXT PRIMARY KEY,
                total_minutes  INTEGER
            );

            CREATE TABLE IF NOT EXISTS fit_daily (
                date            TEXT PRIMARY KEY,
                steps           INTEGER,
                total_calories  REAL,
                avg_hr          REAL,
                min_hr          REAL,
                active_minutes  INTEGER
            );

            CREATE TABLE IF NOT EXISTS user_goals (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                type                 TEXT NOT NULL,
                description          TEXT NOT NULL,
                target               REAL,
                unit                 TEXT,
                exercise_template_id TEXT,
                exercise_name        TEXT,
                muscle_group         TEXT,
                start_value          REAL,
                created_at           TEXT,
                achieved_at          TEXT
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sets_workout ON workout_sets(workout_id);
            CREATE INDEX IF NOT EXISTS idx_sets_template ON workout_sets(exercise_template_id);
            CREATE INDEX IF NOT EXISTS idx_exercises_workout ON workout_exercises(workout_id);
            CREATE INDEX IF NOT EXISTS idx_workouts_start ON workouts(start_time);
        """)


def upsert_workout(workout: dict, db_path: Path = DB_PATH) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO workouts (id, title, description, routine_id, start_time, end_time, updated_at, created_at)
               VALUES (:id, :title, :description, :routine_id, :start_time, :end_time, :updated_at, :created_at)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, description=excluded.description,
                 routine_id=excluded.routine_id, start_time=excluded.start_time,
                 end_time=excluded.end_time, updated_at=excluded.updated_at""",
            {
                "id": workout["id"],
                "title": workout.get("title"),
                "description": workout.get("description"),
                "routine_id": workout.get("routine_id"),
                "start_time": workout.get("start_time"),
                "end_time": workout.get("end_time"),
                "updated_at": workout.get("updated_at"),
                "created_at": workout.get("created_at"),
            },
        )
        conn.execute("DELETE FROM workout_exercises WHERE workout_id=?", (workout["id"],))
        for exercise in workout.get("exercises", []):
            cur = conn.execute(
                """INSERT INTO workout_exercises
                   (workout_id, exercise_template_id, title, notes, idx, superset_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    workout["id"],
                    exercise.get("exercise_template_id"),
                    exercise.get("title"),
                    exercise.get("notes"),
                    exercise.get("index", 0),
                    exercise.get("supersets_id"),
                ),
            )
            we_id = cur.lastrowid
            for s in exercise.get("sets", []):
                conn.execute(
                    """INSERT INTO workout_sets
                       (workout_exercise_id, workout_id, exercise_template_id,
                        idx, type, weight_kg, reps, distance_meters,
                        duration_seconds, rpe, custom_metric)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        we_id,
                        workout["id"],
                        exercise.get("exercise_template_id"),
                        s.get("index", 0),
                        s.get("type"),
                        s.get("weight_kg"),
                        s.get("reps"),
                        s.get("distance_meters"),
                        s.get("duration_seconds"),
                        s.get("rpe"),
                        s.get("custom_metric"),
                    ),
                )


def delete_workout(workout_id: str, db_path: Path = DB_PATH) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM workouts WHERE id=?", (workout_id,))


def upsert_exercise_template(template: dict, db_path: Path = DB_PATH) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO exercise_templates
               (id, title, type, primary_muscle_group, secondary_muscle_groups, is_custom)
               VALUES (:id, :title, :type, :pmg, :smg, :custom)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, type=excluded.type,
                 primary_muscle_group=excluded.primary_muscle_group,
                 secondary_muscle_groups=excluded.secondary_muscle_groups,
                 is_custom=excluded.is_custom""",
            {
                "id": template["id"],
                "title": template.get("title"),
                "type": template.get("type"),
                "pmg": template.get("primary_muscle_group"),
                "smg": json.dumps(template.get("secondary_muscle_groups", [])),
                "custom": int(template.get("is_custom", False)),
            },
        )


def upsert_body_measurement(m: dict, db_path: Path = DB_PATH) -> None:
    fields = [
        "date", "weight_kg", "lean_mass_kg", "fat_percent",
        "neck_cm", "shoulder_cm", "chest_cm",
        "left_bicep_cm", "right_bicep_cm", "left_forearm_cm", "right_forearm_cm",
        "abdomen", "waist", "hips", "left_thigh", "right_thigh", "left_calf", "right_calf",
    ]
    row = {f: m.get(f) for f in fields}
    cols = ", ".join(fields)
    placeholders = ", ".join(f":{f}" for f in fields)
    updates = ", ".join(f"{f}=excluded.{f}" for f in fields if f != "date")
    with _conn(db_path) as conn:
        conn.execute(
            f"INSERT INTO body_measurements ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}",
            row,
        )


def get_sync_state(key: str, db_path: Path = DB_PATH) -> str | None:
    with _conn(db_path) as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_sync_state(key: str, value: str, db_path: Path = DB_PATH) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def query(sql: str, params: tuple = (), db_path: Path = DB_PATH) -> list[dict]:
    with _conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
