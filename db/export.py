"""Data-plane export/import for Settings → Developer.

The interactive flows live in commands/settings.py; this module owns the
JSON dump/restore semantics against the active profile database.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import config

_EXPORT_KINDS: dict[str, list[str] | None] = {
    "memories": ["chat_memories"],
    "goals": ["user_goals"],
    "measurements": ["body_measurements"],
    "full": None,  # every table in the active DB
}


def export_data(kind: str, dest_dir: Path | None = None) -> tuple[Path, int]:
    """Dump the requested tables to a timestamped JSON file. Returns (path, total_rows)."""
    from db.goals import get_token_usage, get_token_usage_month
    from db.store import query as _query

    tables = _EXPORT_KINDS[kind]
    if tables is None:
        tables = [
            r["name"]
            for r in _query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    dumped: dict = {}
    total = 0
    for t in tables:
        try:
            rows = _query(f'SELECT * FROM "{t}"')
        except sqlite3.OperationalError:
            rows = []
        dumped[t] = rows
        total += len(rows)

    payload = {
        "app": "lifter",
        "kind": kind,
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": dumped,
    }
    # user_preferences holds only UI settings and ai_tokens_* counters — API keys
    # live in profile.json / .env, so a full dump needs no redaction.
    if kind in ("goals", "full"):
        payload["token_usage"] = {
            "lifetime": get_token_usage(),
            "month": get_token_usage_month(),
        }

    out_dir = dest_dir or config.export_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Could not create the export folder at {out_dir}: {e}") from e
    path = out_dir / f"lifter-export-{kind}-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path, total


def read_import_payload(path: Path) -> dict:
    """Load and structurally validate an export file. Raises ValueError if not importable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(str(e)[:120]) from e
    if not isinstance(payload, dict) or payload.get("app") != "lifter":
        raise ValueError("missing app == 'lifter' marker")
    tables = payload.get("tables")
    if not isinstance(tables, dict) or not all(isinstance(v, list) for v in tables.values()):
        raise ValueError("missing/invalid 'tables' object")
    return payload


def import_data(path: Path, payload: dict | None = None) -> dict:
    """Restore tables from an export file (replace semantics, single transaction).

    For every dumped table that exists in the current schema: delete all rows,
    then insert the dumped rows. Tables absent from the schema are skipped;
    columns unknown to the schema are dropped per row.
    """
    import db.store as store_mod  # module attrs → honors the tmp_db monkeypatch

    payload = payload or read_import_payload(path)
    store_mod.init_db()  # the profile may have just been reset

    imported: dict = {}
    skipped_tables: list = []
    skipped_columns: dict = {}
    conn = store_mod._conn()
    try:
        with conn:
            live = {}
            for table in payload["tables"]:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                if not cols:
                    skipped_tables.append(table)
                    continue
                live[table] = set(cols)

            # FK checks deferred to commit — dumped tables can be inserted in
            # any order and a violation rolls the whole transaction back.
            # Must be set AFTER the table_info reads: in sqlite3's legacy
            # autocommit handling, a later PRAGMA read silently resets it.
            conn.execute("PRAGMA defer_foreign_keys = ON")

            # Wipe every target table before ANY insert — a cascade delete of an
            # old parent row must never eat freshly inserted children.
            for table in live:
                conn.execute(f'DELETE FROM "{table}"')

            for table, colset in live.items():
                inserted = 0
                for row in payload["tables"][table]:
                    keep = [c for c in row if c in colset]
                    dropped = [c for c in row if c not in colset]
                    if dropped:
                        skipped_columns[table] = sorted(set(skipped_columns.get(table, [])) | set(dropped))
                    if not keep:
                        continue
                    col_sql = ", ".join(f'"{c}"' for c in keep)
                    conn.execute(
                        f'INSERT INTO "{table}" ({col_sql}) VALUES ({", ".join("?" * len(keep))})',
                        [row[c] for c in keep],
                    )
                    inserted += 1
                imported[table] = inserted
    finally:
        conn.close()

    from render_cache import invalidate

    invalidate()
    return {
        "kind": payload.get("kind", "?"),
        "exported_at": payload.get("exported_at", "?"),
        "imported": imported,
        "total": sum(imported.values()),
        "skipped_tables": skipped_tables,
        "skipped_columns": skipped_columns,
    }
