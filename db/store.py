import json
import sqlite3
from pathlib import Path
from typing import Any

import config


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Single SQLite connection factory for the whole app.

    Applies WAL + foreign-key enforcement consistently. All modules should use
    this instead of opening their own connections."""
    conn = sqlite3.connect(db_path if db_path is not None else config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Internal alias kept so the existing test harness (which monkeypatches
# `db.store._conn`) and the rest of this module keep working unchanged.
_conn = connect


def init_db(db_path: Path | None = None) -> None:
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

            CREATE TABLE IF NOT EXISTS chat_memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                summary    TEXT NOT NULL,
                category   TEXT DEFAULT 'general'
            );

            CREATE TABLE IF NOT EXISTS routines (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                notes       TEXT,
                folder_id   INTEGER,
                updated_at  TEXT,
                created_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS routine_exercises (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                routine_id           TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
                exercise_template_id TEXT NOT NULL,
                title                TEXT,
                notes                TEXT,
                rest_seconds         INTEGER,
                idx                  INTEGER,
                superset_id          INTEGER
            );

            CREATE TABLE IF NOT EXISTS routine_sets (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                routine_exercise_id INTEGER NOT NULL REFERENCES routine_exercises(id) ON DELETE CASCADE,
                idx                 INTEGER,
                type                TEXT,
                weight_kg           REAL,
                reps                INTEGER,
                distance_meters     INTEGER,
                duration_seconds    INTEGER,
                custom_metric       REAL
            );

            CREATE INDEX IF NOT EXISTS idx_sets_workout ON workout_sets(workout_id);
            CREATE INDEX IF NOT EXISTS idx_sets_template ON workout_sets(exercise_template_id);
            CREATE INDEX IF NOT EXISTS idx_exercises_workout ON workout_exercises(workout_id);
            CREATE INDEX IF NOT EXISTS idx_workouts_start ON workouts(start_time);
            CREATE INDEX IF NOT EXISTS idx_routine_ex_routine ON routine_exercises(routine_id);
            CREATE INDEX IF NOT EXISTS idx_body_measurements_date ON body_measurements(date);
            CREATE INDEX IF NOT EXISTS idx_user_goals_achieved ON user_goals(achieved_at);
        """)


def upsert_workout(workout: dict, db_path: Path | None = None) -> None:
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


def delete_workout(workout_id: str, db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM workouts WHERE id=?", (workout_id,))


def upsert_exercise_template(template: dict, db_path: Path | None = None) -> None:
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


def upsert_body_measurement(m: dict, db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO body_measurements
               (date, weight_kg, lean_mass_kg, fat_percent,
                neck_cm, shoulder_cm, chest_cm,
                left_bicep_cm, right_bicep_cm, left_forearm_cm, right_forearm_cm,
                abdomen, waist, hips, left_thigh, right_thigh, left_calf, right_calf)
               VALUES
               (:date, :weight_kg, :lean_mass_kg, :fat_percent,
                :neck_cm, :shoulder_cm, :chest_cm,
                :left_bicep_cm, :right_bicep_cm, :left_forearm_cm, :right_forearm_cm,
                :abdomen, :waist, :hips, :left_thigh, :right_thigh, :left_calf, :right_calf)
               ON CONFLICT(date) DO UPDATE SET
                 weight_kg=excluded.weight_kg, lean_mass_kg=excluded.lean_mass_kg,
                 fat_percent=excluded.fat_percent, neck_cm=excluded.neck_cm,
                 shoulder_cm=excluded.shoulder_cm, chest_cm=excluded.chest_cm,
                 left_bicep_cm=excluded.left_bicep_cm, right_bicep_cm=excluded.right_bicep_cm,
                 left_forearm_cm=excluded.left_forearm_cm, right_forearm_cm=excluded.right_forearm_cm,
                 abdomen=excluded.abdomen, waist=excluded.waist, hips=excluded.hips,
                 left_thigh=excluded.left_thigh, right_thigh=excluded.right_thigh,
                 left_calf=excluded.left_calf, right_calf=excluded.right_calf""",
            {f: m.get(f) for f in [
                "date", "weight_kg", "lean_mass_kg", "fat_percent",
                "neck_cm", "shoulder_cm", "chest_cm",
                "left_bicep_cm", "right_bicep_cm", "left_forearm_cm", "right_forearm_cm",
                "abdomen", "waist", "hips", "left_thigh", "right_thigh", "left_calf", "right_calf",
            ]},
        )


def get_sync_state(key: str, db_path: Path | None = None) -> str | None:
    with _conn(db_path) as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_sync_state(key: str, value: str, db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def query(sql: str, params: tuple = (), db_path: Path | None = None) -> list[dict]:
    with _conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def upsert_routine(routine: dict, db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO routines (id, title, notes, folder_id, updated_at, created_at)
               VALUES (:id, :title, :notes, :folder_id, :updated_at, :created_at)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, notes=excluded.notes,
                 folder_id=excluded.folder_id, updated_at=excluded.updated_at""",
            {
                "id": routine["id"],
                "title": routine.get("title"),
                "notes": routine.get("notes"),
                "folder_id": routine.get("folder_id"),
                "updated_at": routine.get("updated_at"),
                "created_at": routine.get("created_at"),
            },
        )
        conn.execute("DELETE FROM routine_exercises WHERE routine_id=?", (routine["id"],))
        for idx, exercise in enumerate(routine.get("exercises", [])):
            cur = conn.execute(
                """INSERT INTO routine_exercises
                   (routine_id, exercise_template_id, title, notes, rest_seconds, idx, superset_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    routine["id"],
                    exercise.get("exercise_template_id"),
                    exercise.get("title"),
                    exercise.get("notes"),
                    exercise.get("rest_seconds"),
                    exercise.get("index", idx),
                    exercise.get("superset_id"),
                ),
            )
            re_id = cur.lastrowid
            for s_idx, s in enumerate(exercise.get("sets", [])):
                conn.execute(
                    """INSERT INTO routine_sets
                       (routine_exercise_id, idx, type, weight_kg, reps,
                        distance_meters, duration_seconds, custom_metric)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        re_id,
                        s.get("index", s_idx),
                        s.get("type"),
                        s.get("weight_kg"),
                        s.get("reps"),
                        s.get("distance_meters"),
                        s.get("duration_seconds"),
                        s.get("custom_metric"),
                    ),
                )


def delete_routine(routine_id: str, db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM routines WHERE id=?", (routine_id,))


def delete_stale_routines(keep_ids: set, db_path: Path | None = None) -> int:
    """Delete routines whose IDs are not in keep_ids. Returns count deleted."""
    with _conn(db_path) as conn:
        local_ids = {r[0] for r in conn.execute("SELECT id FROM routines").fetchall()}
        stale = local_ids - {str(i) for i in keep_ids}
        for sid in stale:
            conn.execute("DELETE FROM routines WHERE id=?", (sid,))
        return len(stale)


def get_routines_with_exercises(db_path: Path | None = None) -> list[dict]:
    """Return all routines with their exercises and sets via a single JOIN query."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT r.id, r.title, r.notes,
                      re.id AS re_id, re.idx AS re_idx,
                      COALESCE(re.title, et.title, re.exercise_template_id) AS ex_title,
                      re.notes AS ex_notes, re.rest_seconds,
                      rs.type AS set_type, rs.weight_kg, rs.reps, rs.idx AS rs_idx
               FROM routines r
               LEFT JOIN routine_exercises re ON re.routine_id = r.id
               LEFT JOIN exercise_templates et ON et.id = re.exercise_template_id
               LEFT JOIN routine_sets rs ON rs.routine_exercise_id = re.id
               ORDER BY r.title, re.idx, rs.idx"""
        ).fetchall()

    routines_map: dict = {}
    exercises_map: dict = {}
    for row in rows:
        row = dict(row)
        rid = row["id"]
        if rid not in routines_map:
            routines_map[rid] = {"id": rid, "title": row["title"], "notes": row["notes"], "exercises": []}
        re_id = row["re_id"]
        if re_id is None:
            continue
        if re_id not in exercises_map:
            ex: dict = {
                "id": re_id, "idx": row["re_idx"], "title": row["ex_title"],
                "notes": row["ex_notes"], "rest_seconds": row["rest_seconds"], "sets": [],
            }
            exercises_map[re_id] = ex
            routines_map[rid]["exercises"].append(ex)
        if row["set_type"] is not None:
            exercises_map[re_id]["sets"].append(
                {"type": row["set_type"], "weight_kg": row["weight_kg"], "reps": row["reps"]}
            )
    return list(routines_map.values())
