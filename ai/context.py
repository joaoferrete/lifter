"""Prompt-context formatters — turn stored data into text for the AI system prompt.

These live in the AI layer (not in db/) because they are presentation concerns:
they decide what the model sees, apply prompt-injection sanitization, and know
nothing about persistence beyond the public db APIs they call.
"""

from datetime import UTC, datetime

from ai.sanitize import sanitize_for_prompt
from analytics.e1rm import NORMAL_SET_FILTER_SQL, e1rm_sql
from analytics.frequency import muscle_group_frequency, workout_frequency
from analytics.progression import detect_plateaus, top_progressions
from analytics.records import body_measurement_trend, recent_prs
from analytics.volume import muscle_group_summary, sets_per_muscle_per_week
from db.goals import get_goals, get_pref
from db.memories import MEMORY_SUMMARY_MAX_LEN, get_recent_memories
from db.store import get_routines_with_exercises, query


def goals_context_for_ai(weeks: int = 8) -> str:
    """Return a text summary of goals + current progress for the AI system prompt."""
    goals = get_goals()
    if not goals:
        return "No goals set."

    from analytics.goal_progress import compute_goal_progress

    progress = compute_goal_progress()
    prog_by_id = {p["id"]: p for p in progress}

    lines = ["## User goals"]
    for g in goals:
        p = prog_by_id.get(g["id"], {})
        current = p.get("current")
        pct = p.get("pct")
        pct_str = f" ({pct:.0f}%)" if pct is not None else ""
        current_str = f" — current: {current} {g.get('unit') or ''}" if current is not None else ""
        safe_desc = sanitize_for_prompt(g["description"], max_len=150)
        lines.append(f"  - {safe_desc}{current_str}{pct_str}")

    return "\n".join(lines)


def memories_as_context(limit: int = 15) -> str:
    """Recent coach memories formatted (and sanitized) for the system prompt."""
    memories = get_recent_memories(limit)
    if not memories:
        return ""

    lines = ["## Coach memory (from previous conversations)"]
    for m in memories:
        date = (m.get("created_at") or "")[:10]
        safe_summary = sanitize_for_prompt(m["summary"], max_len=MEMORY_SUMMARY_MAX_LEN)
        lines.append(f"  - [{date}] {safe_summary}")
    return "\n".join(lines)


def _build_context(weeks: int = 8, slim: bool = False, include_routine: bool = True, full_library: bool = False) -> str:
    send_name = get_pref("ai_send_name") != "0"
    name = (get_pref("display_name") if send_name else None) or "the athlete"
    freq = workout_frequency(weeks)
    muscle_vol = muscle_group_summary(weeks)
    muscle_freq = muscle_group_frequency(weeks)
    sets_per_week = sets_per_muscle_per_week(weeks)
    plateaus = detect_plateaus(weeks)
    top_gains = top_progressions(weeks)
    prs = recent_prs(30)
    body = body_measurement_trend(weeks)

    # Exercise library and saved routines only matter when the model must build or
    # reference a routine — skip them otherwise to save input tokens.
    #
    # By default the library lists only exercises the athlete has actually performed
    # (cheap, a few dozen rows). The full 433-row catalogue is far heavier (~8k tokens),
    # so it is injected only when explicitly generating a routine in a path that cannot
    # use tools (full_library=True). Interactive chat keeps the lean list and reaches the
    # rest of the catalogue on demand via the find_exercises tool.
    if not include_routine:
        known = []
    elif full_library:
        known = query(
            """SELECT id, title, type, primary_muscle_group
               FROM exercise_templates
               ORDER BY primary_muscle_group, title"""
        )
    else:
        known = query(
            """SELECT DISTINCT et.id, et.title, et.type, et.primary_muscle_group
               FROM workout_exercises we
               JOIN exercise_templates et ON et.id = we.exercise_template_id
               ORDER BY et.primary_muscle_group, et.title"""
        )

    safe_name = sanitize_for_prompt(name, max_len=60)

    now = datetime.now(UTC).astimezone()
    current_datetime_line = f"- Current date/time: {now.strftime('%A, %Y-%m-%d %H:%M')} (local)"

    # Days since last workout — helps the coach assess recovery state
    last_wkt = query("SELECT start_time FROM workouts ORDER BY start_time DESC LIMIT 1")
    days_since_last = None
    if last_wkt:
        try:
            last_dt = datetime.fromisoformat(last_wkt[0]["start_time"].replace("Z", "+00:00"))
            days_since_last = (datetime.now(UTC) - last_dt).days
        except Exception as e:
            from debug_log import error

            error("AI", "unparseable start_time in last workout", exc=e)

    lines = [
        f"## Athlete: {safe_name}",
        current_datetime_line,
        f"## Training summary (last {weeks} weeks)\n",
        f"- Total workouts: {freq['total_workouts']}",
        f"- Avg workouts/week: {freq['avg_per_week']}",
        f"- Avg session duration: {freq['avg_duration_minutes']} min",
        f"- Avg rest days between sessions: {freq['rest_day_avg']}",
    ]
    if days_since_last is not None:
        lines.append(f"- Days since last workout: {days_since_last}")
    lines += [
        "",
        "## Weekly volume (avg kg tonnage) by muscle group",
    ]
    for muscle, vol in muscle_vol.items():
        sessions = muscle_freq.get(muscle, 0)
        sets_wk = sets_per_week.get(muscle, 0)
        lines.append(f"  - {muscle}: {vol} kg/week ({sessions:.1f} sessions/wk, {sets_wk:.1f} sets/wk)")

    if body and get_pref("ai_send_body") != "0":
        lines += [
            "",
            "## Body measurements",
            f"  - Weight: {body.get('weight_kg')} kg (change: {body.get('weight_change_kg', 'N/A')} kg)",
            f"  - Body fat: {body.get('fat_percent')}% (change: {body.get('fat_change_pct', 'N/A')}%)",
        ]
        from analytics.records import compute_bmi
        from db.goals import get_height_cm

        _height_cm = get_height_cm()
        _bmi = compute_bmi(body.get("weight_kg"), _height_cm)
        if _bmi is not None:
            lines.append(f"  - Height: {_height_cm} cm | BMI: {_bmi}")

    if prs:
        lines += ["", "## Personal records set in last 30 days"]
        for pr in prs[:8]:
            lines.append(
                f"  - {pr['exercise']}: {pr['weight_kg']}kg × {pr['reps']} reps (e1RM {pr['e1rm']} kg) on {pr['date']}"
            )

    if top_gains:
        lines += ["", "## Top improvements this period"]
        for g in top_gains:
            lines.append(
                f"  - {g['exercise']}: +{g['improvement_pct']}% (e1RM {g['start_e1rm']} → {g['current_e1rm']} kg)"
            )

    if plateaus:
        lines += ["", "## Exercises showing a plateau"]
        for p in plateaus:
            lines.append(
                f"  - {p['exercise']}: no progress in last {p['sessions_stalled']} sessions (e1RM {p['current_e1rm']} kg)"
            )

    lines += ["", goals_context_for_ai(weeks)]

    # Include current goals with IDs so the AI can reference them for updates
    active_goals = get_goals()
    if active_goals:
        lines += ["", "## Active goals with IDs (use these IDs in manage_goals)"]
        for g in active_goals:
            safe_desc = sanitize_for_prompt(g["description"], max_len=150)
            lines.append(f"  - id={g['id']} | {safe_desc} | target={g['target']} {g.get('unit') or ''}")

    # Fit / recovery data — optional; the context must build without it.
    try:
        from fit.analytics import fit_context_for_ai

        fit_ctx = fit_context_for_ai(7)
        if "No Google Fit" not in fit_ctx:
            lines += ["", fit_ctx]
    except Exception as e:
        from debug_log import error

        error("AI", "Fit context skipped", exc=e)

    # Memories from past conversations — likewise optional.
    try:
        mem_ctx = memories_as_context()
        if mem_ctx:
            lines += ["", mem_ctx]
    except Exception as e:
        from debug_log import error

        error("AI", "memories context skipped", exc=e)

    # Recent workouts — the actual sessions with exercises and best sets.
    # This is the most important near-term context: what did the athlete do
    # last, how heavy, and how long ago.
    recent_wkts = query(
        f"""SELECT id, title, start_time, end_time
           FROM workouts
           ORDER BY start_time DESC
           LIMIT {"5" if slim else "7"}"""
    )
    if recent_wkts:
        lines += ["", "## Recent workouts (last sessions, newest first)"]
        for w in recent_wkts:
            try:
                start_dt = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
                date_str = start_dt.strftime("%a %d %b %Y")
                end_dt = datetime.fromisoformat(w["end_time"].replace("Z", "+00:00"))
                dur = int((end_dt - start_dt).total_seconds() / 60)
                dur_str = f"{dur} min"
            except Exception:
                date_str = (w["start_time"] or "")[:10]
                dur_str = ""

            # Workout/exercise titles come from Hevy (external) — sanitize like
            # every other externally-sourced string before prompt insertion.
            lines.append(f"\n  {sanitize_for_prompt(w['title'] or '', max_len=100)} — {date_str} ({dur_str})")

            # Best normal set per exercise in this workout
            ex_rows = query(
                f"""SELECT we.title,
                          ws.weight_kg,
                          ws.reps,
                          {e1rm_sql()} AS e1rm
                   FROM workout_exercises we
                   JOIN workout_sets ws ON ws.workout_exercise_id = we.id
                   WHERE we.workout_id = ?
                     AND {NORMAL_SET_FILTER_SQL}
                   ORDER BY we.idx, e1rm DESC""",
                (w["id"],),
            )
            # One best set per exercise name
            seen_ex: dict = {}
            for row in ex_rows:
                if row["title"] not in seen_ex:
                    seen_ex[row["title"]] = row

            if seen_ex:
                for ex_title, row in seen_ex.items():
                    safe_title = sanitize_for_prompt(ex_title or "", max_len=100)
                    lines.append(
                        f"    - {safe_title}: {row['weight_kg']} kg × {row['reps']} reps (e1RM {row['e1rm']:.1f} kg)"
                    )
            else:
                # Bodyweight / cardio session — just list exercise names
                bw = query(
                    "SELECT DISTINCT we.title FROM workout_exercises we WHERE we.workout_id = ?",
                    (w["id"],),
                )
                for b in bw:
                    lines.append(f"    - {sanitize_for_prompt(b['title'] or '', max_len=100)}")

    saved_routines = get_routines_with_exercises() if include_routine else []
    if saved_routines:
        lines += ["", f"## Saved routines ({len(saved_routines)} total)"]
        for r in saved_routines:
            lines.append(f"\n  ### {sanitize_for_prompt(r['title'] or '', max_len=100)} (id: {r['id']})")
            if r.get("notes"):
                lines.append(f"  [notes: {sanitize_for_prompt(r['notes'], max_len=120)}]")
            for ex in r.get("exercises", []):
                normal_sets = [s for s in ex["sets"] if s.get("type") == "normal"]
                set_desc = ""
                if normal_sets:
                    reps_list = [str(s["reps"]) for s in normal_sets if s.get("reps")]
                    weight = next((s["weight_kg"] for s in normal_sets if s.get("weight_kg")), None)
                    count = len(normal_sets)
                    if weight:
                        set_desc = f" — {count}×{reps_list[0] if reps_list else '?'} @ {weight}kg"
                    elif reps_list:
                        set_desc = f" — {count}×{reps_list[0]}"
                lines.append(f"    - {sanitize_for_prompt(ex['title'] or '', max_len=100)}{set_desc}")

    if known:
        if full_library:
            lines += ["", "## Exercise library (use these IDs in routines)"]
        else:
            lines += [
                "",
                "## Exercise library — exercises the athlete has performed (use these IDs in routines)",
                "  (This lists only previously-used exercises. For any OTHER exercise — e.g. a cardio"
                " machine like bike/elliptical/treadmill, or a new variation — call the find_exercises"
                " tool to look up its exercise_template_id before using it.)",
            ]
        for ex in known:
            lines.append(
                f"  - {ex['title']} | id: {ex['id']} | type: {ex['type']} | muscle: {ex['primary_muscle_group']}"
            )

    return "\n".join(lines)
