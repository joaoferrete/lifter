"""Recovery and health analytics from Google Fit data."""
from db.store import query


def sleep_summary(days: int = 7) -> dict:
    rows = query(
        """SELECT date, total_minutes FROM fit_sleep
           WHERE date >= date('now', ?)
           ORDER BY date DESC""",
        (f"-{days} days",),
    )
    if not rows:
        return {}

    minutes = [r["total_minutes"] for r in rows if r["total_minutes"]]
    if not minutes:
        return {}

    avg_min = sum(minutes) / len(minutes)
    nights_7plus = sum(1 for m in minutes if m >= 420)  # 7h = 420 min

    return {
        "avg_hours": round(avg_min / 60, 1),
        "nights_tracked": len(minutes),
        "nights_7plus_hours": nights_7plus,
        "consistency_pct": round(nights_7plus / len(minutes) * 100),
        "last_night_hours": round(minutes[0] / 60, 1) if minutes else None,
    }


def activity_summary(days: int = 7) -> dict:
    rows = query(
        """SELECT date, steps, total_calories, avg_hr, min_hr, active_minutes
           FROM fit_daily
           WHERE date >= date('now', ?)
           ORDER BY date DESC""",
        (f"-{days} days",),
    )
    if not rows:
        return {}

    def avg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    return {
        "avg_steps": avg("steps"),
        "avg_calories": avg("total_calories"),
        "avg_hr": avg("avg_hr"),
        "resting_hr": avg("min_hr"),   # minimum HR is a proxy for resting HR
        "avg_active_minutes": avg("active_minutes"),
        "days_tracked": len(rows),
    }


def recovery_score(days: int = 3) -> dict | None:
    """Simple recovery score (0-100) based on recent sleep and resting HR."""
    sleep = sleep_summary(days)
    activity = activity_summary(days)

    if not sleep or not activity:
        return None

    # Sleep component (0-50): 8h = full score
    sleep_hours = sleep.get("avg_hours", 0)
    sleep_pts = min(sleep_hours / 8 * 50, 50)

    # HR component (0-50): lower resting HR = better recovery
    # baseline assumed at 65 bpm; every bpm below = +1pt (capped)
    rhr = activity.get("resting_hr")
    if rhr:
        hr_pts = max(0, min(50, 50 - (rhr - 50)))
    else:
        hr_pts = 25  # neutral if no data

    score = int(sleep_pts + hr_pts)
    label = (
        "Excellent" if score >= 80
        else "Good"    if score >= 65
        else "Fair"    if score >= 45
        else "Poor"
    )
    color = (
        "green"  if score >= 80
        else "cyan"    if score >= 65
        else "yellow"  if score >= 45
        else "red"
    )

    return {
        "score": score,
        "label": label,
        "color": color,
        "sleep_hours": sleep_hours,
        "resting_hr": rhr,
    }


def fit_context_for_ai(days: int = 7) -> str:
    """Return a text block for the AI system prompt."""
    sleep = sleep_summary(days)
    activity = activity_summary(days)
    recovery = recovery_score(3)

    if not sleep and not activity:
        return "No Google Fit data available."

    lines = [f"## Health & Recovery (last {days} days from Google Fit)"]

    if sleep:
        lines += [
            f"- Avg sleep: {sleep['avg_hours']}h/night  ({sleep['nights_7plus_hours']}/{sleep['nights_tracked']} nights ≥7h)",
            f"- Last night: {sleep.get('last_night_hours')}h",
        ]

    if activity:
        if activity.get("avg_steps"):
            lines.append(f"- Avg steps/day: {int(activity['avg_steps']):,}")
        if activity.get("avg_calories"):
            lines.append(f"- Avg total calories/day: {int(activity['avg_calories']):,} kcal")
        if activity.get("resting_hr"):
            lines.append(f"- Resting HR: {activity['resting_hr']} bpm")
        if activity.get("avg_active_minutes"):
            lines.append(f"- Avg active minutes/day: {int(activity['avg_active_minutes'])}")

    if recovery:
        lines.append(f"- Recovery score: {recovery['score']}/100 ({recovery['label']})")

    return "\n".join(lines)
