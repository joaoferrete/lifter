"""Shared Rich console, questionary style, and score→color scale."""

import questionary
from rich.console import Console

console = Console()

STYLE = questionary.Style(
    [
        ("qmark", "fg:#00d7ff bold"),
        ("question", "bold"),
        ("answer", "fg:#00d7ff bold"),
        ("pointer", "fg:#00d7ff bold"),
        ("highlighted", "fg:#00d7ff bold"),
        ("selected", "fg:#00d7ff"),
        ("separator", "fg:#555555"),
        ("instruction", "fg:#555555 italic"),
        ("checkbox", "fg:#00d7ff"),
    ]
)


def score_color(score: int) -> str:
    """The one 'how good is this 0-100 value' color scale, used everywhere."""
    if score >= 80:
        return "green"
    if score >= 60:
        return "cyan"
    if score >= 40:
        return "yellow"
    return "red"
