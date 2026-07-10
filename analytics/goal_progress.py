"""Progress computation for user goals.

Sits above both the goals CRUD (db.goals) and the analytics modules: each goal
type is measured against workout/body data and newly-achieved goals are marked.
Lives here rather than in db/ so the storage layer never imports analytics.
"""

from db.goals import get_goals, mark_goal_achieved, update_goal_fields


def compute_goal_progress() -> list[dict]:
    """Compute current progress for every active goal. Marks achieved goals.

    Memoized per active DB (invalidated on goal edits and sync) — this runs on
    every menu/snapshot render and each goal triggers analytics queries."""
    from render_cache import cached

    return cached("goal_progress", _compute_goal_progress)


def _compute_goal_progress() -> list[dict]:
    from db.store import query

    goals = get_goals()
    if not goals:
        return []

    results = []
    newly_achieved: list[int] = []

    for goal in goals:
        result: dict = {
            "id": goal["id"],
            "type": goal["type"],
            "description": goal["description"],
            "target": goal["target"],
            "unit": goal["unit"] or "",
            "current": None,
            "pct": 0.0,
            "achieved": False,
            "exercise_name": goal.get("exercise_name"),
        }

        try:
            if goal["type"] == "lift_pr":
                rows = query(
                    """SELECT MAX(ws.weight_kg * (1 + ws.reps / 30.0)) as e1rm
                       FROM workout_sets ws
                       WHERE ws.exercise_template_id = ?
                         AND ws.type = 'normal'
                         AND ws.weight_kg IS NOT NULL AND ws.reps IS NOT NULL""",
                    (goal["exercise_template_id"],),
                )
                current = float(rows[0]["e1rm"] or 0) if rows else 0.0
                target = goal["target"] or 1.0
                result["current"] = round(current, 1)
                result["pct"] = min(current / target * 100, 100)
                result["achieved"] = current >= target

            elif goal["type"] == "frequency":
                from analytics.frequency import workout_frequency

                current = float(workout_frequency(4)["avg_per_week"])
                target = goal["target"] or 1.0
                result["current"] = round(current, 1)
                result["pct"] = min(current / target * 100, 100)
                result["achieved"] = current >= target

            elif goal["type"] in ("weight_loss", "weight_gain"):
                rows = query(
                    "SELECT weight_kg FROM body_measurements WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
                )
                if rows:
                    current = float(rows[0]["weight_kg"])
                    target = goal["target"] or current
                    # Capture the baseline once if it was never stored (e.g. AI-created
                    # goals), so progress isn't stuck at 0 with start == current forever.
                    if goal["start_value"] is None:
                        update_goal_fields(goal["id"], start_value=current)
                        start = current
                    else:
                        start = float(goal["start_value"])
                    result["current"] = current
                    result["start"] = start
                    # Compute even when the athlete moved the wrong way, so progress can
                    # go negative (no max(0, ...) floor). Only cap the top at 100%.
                    if start != target:
                        if goal["type"] == "weight_loss":
                            result["pct"] = min((start - current) / (start - target) * 100, 100)
                            result["achieved"] = current <= target
                        else:  # weight_gain
                            result["pct"] = min((current - start) / (target - start) * 100, 100)
                            result["achieved"] = current >= target

            elif goal["type"] == "body_fat":
                rows = query(
                    "SELECT fat_percent FROM body_measurements WHERE fat_percent IS NOT NULL ORDER BY date DESC LIMIT 1"
                )
                if rows:
                    current = float(rows[0]["fat_percent"])
                    target = goal["target"] or current
                    if goal["start_value"] is None:
                        update_goal_fields(goal["id"], start_value=current)
                        start = current
                    else:
                        start = float(goal["start_value"])
                    result["current"] = current
                    result["start"] = start
                    if start != target:
                        result["pct"] = min((start - current) / (start - target) * 100, 100)
                        result["achieved"] = current <= target

            elif goal["type"] == "volume":
                from analytics.volume import sets_per_muscle_per_week

                current = float(sets_per_muscle_per_week(4).get(goal["muscle_group"] or "", 0))
                target = goal["target"] or 1.0
                result["current"] = round(current, 1)
                result["pct"] = min(current / target * 100, 100)
                result["achieved"] = current >= target

            elif goal["type"] == "custom":
                result["pct"] = None  # no numeric progress; shown as text only

        except Exception as e:
            # A failure in one goal must not hide the others, but leave a trail.
            from debug_log import error

            error("GOALS", f"progress computation failed for goal {goal['id']} ({goal['type']})", exc=e)

        if result["achieved"]:
            newly_achieved.append(goal["id"])

        results.append(result)

    for gid in newly_achieved:
        mark_goal_achieved(gid)

    return results
