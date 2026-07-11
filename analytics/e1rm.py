"""Canonical estimated-1RM (Epley) — the single definition used everywhere.

A true single (reps == 1) IS the 1RM: applying Epley would inflate it by ~3.3%.
Historically the Python helpers special-cased singles while inline SQL didn't,
so PRs and goal progress disagreed for 1-rep sets. Any new e1RM computation
must go through `e1rm()` or `e1rm_sql()`.
"""

# Working sets that can produce an e1RM. Assumes the standard `ws` alias for
# workout_sets (or routine_sets) in the surrounding query.
NORMAL_SET_FILTER_SQL = "ws.type = 'normal' AND ws.weight_kg IS NOT NULL AND ws.reps IS NOT NULL AND ws.reps > 0"


def e1rm(weight_kg: float, reps: int) -> float:
    """Epley estimated 1RM; a true single returns the raw weight."""
    if reps == 1:
        return weight_kg
    return weight_kg * (1 + reps / 30)


def e1rm_sql(weight_col: str = "ws.weight_kg", reps_col: str = "ws.reps") -> str:
    """SQL expression equivalent to e1rm() for embedding in SELECTs."""
    return f"(CASE WHEN {reps_col} = 1 THEN {weight_col} ELSE {weight_col} * (1 + {reps_col} / 30.0) END)"
