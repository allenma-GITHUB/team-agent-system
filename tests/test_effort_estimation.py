#!/usr/bin/env python3
"""
Test DepartmentManager.estimate_hours(): a keyword-based default effort
estimate for tasks submitted without an explicit --hours. Before this,
every task defaulted to a flat 1.0h regardless of what it actually said,
so "fix a typo" and "full platform migration" would be billed and measured
identically unless a human remembered to pass --hours by hand.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from departments import DepartmentManager
from main_v2 import submit_task, TASKS_FILE, init_system
import json
import tempfile
import os


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_low_effort_keywords_estimate_short():
    print_section("1. Low-Effort Keywords Estimate Short")

    for desc in ["Fix a typo in the footer", "Quick tweak to the button color", "Trivial copy change"]:
        hours = DepartmentManager.estimate_hours(desc)
        print(f"  '{desc}' -> {hours}h")
        assert hours == 0.5


def test_high_effort_keywords_estimate_long():
    print_section("2. High-Effort Keywords Estimate Long")

    for desc in ["Full database platform migration", "Redesign the checkout flow", "Rewrite the auth module"]:
        hours = DepartmentManager.estimate_hours(desc)
        print(f"  '{desc}' -> {hours}h")
        assert hours == 16.0


def test_neutral_description_falls_back_to_default():
    print_section("3. Neutral Description Falls Back to Default")

    hours = DepartmentManager.estimate_hours("Review a pull request")
    print(f"  'Review a pull request' -> {hours}h")
    assert hours == 1.0


def test_high_effort_keyword_wins_if_both_present():
    """If a description somehow matches both tiers, high-effort should win -
    understating a genuinely large task is the worse failure mode."""
    print_section("4. Ambiguous Description: High-Effort Takes Priority")

    hours = DepartmentManager.estimate_hours("A quick-sounding platform migration")
    print(f"  'A quick-sounding platform migration' -> {hours}h")
    assert hours == 16.0


def test_submit_task_uses_the_heuristic_by_default():
    """submit_task() with no estimated_hours should use the heuristic, not a flat 1.0h."""
    print_section("5. submit_task() Uses The Heuristic By Default")

    with tempfile.TemporaryDirectory() as tmp:
        original_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            init_system()
            submit_task("Fix a typo in the header", department="engineering")
            submit_task("Full platform migration", department="engineering")
            submit_task("Ordinary task", department="engineering", estimated_hours=42.0)

            tasks = json.loads(TASKS_FILE.read_text())
            hours_by_desc = {t["description"]: t["estimated_hours"] for t in tasks}
            print(f"  {hours_by_desc}")

            assert hours_by_desc["Fix a typo in the header"] == 0.5
            assert hours_by_desc["Full platform migration"] == 16.0
            assert hours_by_desc["Ordinary task"] == 42.0  # explicit override still wins
        finally:
            os.chdir(original_cwd)


def main():
    print("\n" + "=" * 60)
    print("  EFFORT ESTIMATION HEURISTIC DEMONSTRATION")
    print("=" * 60)

    test_low_effort_keywords_estimate_short()
    test_high_effort_keywords_estimate_long()
    test_neutral_description_falls_back_to_default()
    test_high_effort_keyword_wins_if_both_present()
    test_submit_task_uses_the_heuristic_by_default()

    print("\n" + "=" * 60)
    print("  [OK] All effort-estimation tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
