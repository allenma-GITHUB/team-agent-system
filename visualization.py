"""
Visualization - zero-dependency ASCII bar charts for the CLI reports.

Every number in this system already lives in report()/status() as plain
text ("73%", "4.2/5.0"), but a wall of numbers doesn't show *relative*
standing at a glance the way a bar does - is engineering's budget nearly
gone while design's sits untouched? This adds exactly one reusable
primitive (a horizontal bar) rather than pulling in a charting library;
the rest of the project deliberately has no third-party dependencies, and
a bar made of block characters doesn't need one either.
"""
from typing import List, Tuple

FILLED = "█"
EMPTY = "░"
OVER_MARKER = "!"  # flags a fraction > 1.0 (e.g. an over-budget department)


def render_bar(label: str, fraction: float, display: str,
               width: int = 24, label_width: int = 18) -> str:
    """Render one horizontal bar line: "  label [####....] ! display".

    `fraction` drives the bar's fill and is clamped to [0, 1] for that
    purpose only - a genuinely over-100% value (over-budget spend, an
    over-capacity department) still draws a full bar, marked with
    OVER_MARKER, rather than silently clamping or corrupting the number
    itself. `display` is the caller's own formatted value (a percentage,
    a "4.2/5.0" score, a dollar amount) - this function never formats
    numbers itself, so it stays reusable across very different units.
    """
    clamped = max(0.0, min(1.0, fraction))
    filled = round(width * clamped)
    bar = FILLED * filled + EMPTY * (width - filled)
    marker = OVER_MARKER if fraction > 1.0 else " "
    return f"  {label:<{label_width}} [{bar}] {marker} {display}"


def bar_chart(rows: List[Tuple[str, float, str]], width: int = 24, label_width: int = 18) -> str:
    """Render a multi-line bar chart from (label, fraction, display) rows.

    Rows are drawn in the order given - sort them first if a particular
    ranking (highest first, alphabetical, ...) matters to the caller.
    Returns "" for an empty list so callers can join it in unconditionally.

    `label_width` is a floor, not a fixed width: it widens to fit the
    longest label in this particular chart so every bar in it still lines
    up in the same column, instead of the one row with a long label (e.g.
    "Engineering Manager") pushing its own bracket out of line with the
    shorter labels around it.
    """
    if not rows:
        return ""
    label_width = max(label_width, max(len(label) for label, _, _ in rows))
    return "\n".join(render_bar(label, fraction, display, width, label_width) for label, fraction, display in rows)
