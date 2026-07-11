"""Small reusable render helpers (bars, score rows)."""

from ui.console import score_color


def bar(value: float, total: float, width: int, color: str = "cyan", *, min_filled: int = 0) -> str:
    """A filled/empty block bar like '████░░░░' with Rich color markup.

    Replaces the half-dozen hand-rolled '█' * n + '░' * (w - n) variants that
    each clamped slightly differently.
    """
    filled = 0 if total <= 0 else round(value / total * width)
    filled = max(min_filled, min(width, filled))
    return f"[{color}]{'█' * filled}[/{color}][dim]{'░' * (width - filled)}[/dim]"


def score_bar(label: str, score: int, bar_width: int = 12) -> str:
    """A labelled 0-100 score bar in the score color scale."""
    color = score_color(score)
    return f"[bold]{label:<10}[/bold] {bar(score, 100, bar_width, color, min_filled=1)}  [{color}][bold]{score:>3}[/bold][/{color}]"
