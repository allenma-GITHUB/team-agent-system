#!/usr/bin/env python3
"""
Test visualization.py: the zero-dependency ASCII bar chart helper added to
give report()/status() a quick relative-standing view (department budget
utilization, agent quality) instead of only a column of numbers.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visualization import render_bar, bar_chart, FILLED, EMPTY, OVER_MARKER


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_render_bar_fill_matches_fraction():
    """A fraction of 0.5 at width=10 should fill exactly half the bar."""
    print_section("1. Bar Fill Matches Fraction")

    line = render_bar("engineering", 0.5, "50%", width=10, label_width=15)
    print(f"  {line!r}")

    assert line.count(FILLED) == 5
    assert line.count(EMPTY) == 5
    assert "engineering" in line
    assert "50%" in line


def test_render_bar_clamps_zero_and_full():
    print_section("2. Bar Clamps At Empty And Full")

    empty_line = render_bar("idle", 0.0, "0%", width=10, label_width=10)
    full_line = render_bar("maxed", 1.0, "100%", width=10, label_width=10)
    print(f"  {empty_line!r}")
    print(f"  {full_line!r}")

    assert empty_line.count(FILLED) == 0
    assert full_line.count(FILLED) == 10
    assert OVER_MARKER not in full_line  # exactly 100% is full, not "over"


def test_render_bar_marks_over_100_percent_without_corrupting_the_bar():
    """An over-budget department (utilization > 1.0) should still draw a
    full bar, flagged with OVER_MARKER, instead of crashing or drawing a
    bar longer than `width`."""
    print_section("3. Over-100% Fraction Draws A Full Bar, Marked")

    line = render_bar("support", 1.4, "140% ($1,400/$1,000)", width=10, label_width=10)
    print(f"  {line!r}")

    assert line.count(FILLED) == 10  # clamped, not overflowing
    assert OVER_MARKER in line
    assert "140%" in line


def test_bar_chart_renders_one_line_per_row_in_given_order():
    print_section("4. Bar Chart Renders Rows In Given Order, One Line Each")

    rows = [
        ("design", 0.9, "90%"),
        ("engineering", 0.3, "30%"),
        ("support", 1.1, "110%"),
    ]
    chart = bar_chart(rows, width=10, label_width=12)
    print(chart)
    lines = chart.split("\n")

    assert len(lines) == 3
    assert "design" in lines[0]
    assert "engineering" in lines[1]
    assert "support" in lines[2]
    assert OVER_MARKER in lines[2]


def test_bar_chart_empty_rows_is_empty_string():
    """Callers append bar_chart(...) unconditionally into a report list -
    it must not inject stray blank lines when there's nothing to show."""
    print_section("5. Empty Rows Produce An Empty String, Not A Blank Line")

    result = bar_chart([])
    print(f"  {result!r}")
    assert result == ""


if __name__ == "__main__":
    test_render_bar_fill_matches_fraction()
    test_render_bar_clamps_zero_and_full()
    test_render_bar_marks_over_100_percent_without_corrupting_the_bar()
    test_bar_chart_renders_one_line_per_row_in_given_order()
    test_bar_chart_empty_rows_is_empty_string()

    # Run twice back-to-back: this module is pure/stateless, so results
    # must be identical both times.
    test_render_bar_fill_matches_fraction()
    test_render_bar_clamps_zero_and_full()
    test_render_bar_marks_over_100_percent_without_corrupting_the_bar()
    test_bar_chart_renders_one_line_per_row_in_given_order()
    test_bar_chart_empty_rows_is_empty_string()

    print("\n✅ All visualization tests passed!\n")
