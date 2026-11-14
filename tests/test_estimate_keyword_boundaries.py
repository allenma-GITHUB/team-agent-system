#!/usr/bin/env python3
"""
Checkpoint 97. DepartmentManager.estimate_hours()/estimate_complexity()/
estimate_urgency() matched their keyword tiers with plain `kw in desc_lower`
substring containment, on the documented claim (departments.py's old comment,
now corrected) that their keywords are "long enough that an accidental
substring hit is implausible." False for real English words: "typo" is a
substring of "typography"/"typology", "migration" is a substring of
"immigration".

Confirmed live before the fix, direct import, no mocking:
    estimate_hours("Update the typography and font sizes ...")       -> 0.5
    estimate_hours("Review the new immigration policy ...")          -> 16.0
    estimate_complexity("Update the typography and font sizes ...")  -> 0.2
    estimate_complexity("Review the new immigration policy ...")     -> 0.9
A design-system typography task was silently billed/measured as a trivial
0.5h typo fix; a support/policy-review task about immigration was silently
treated as a 16-hour, high-complexity platform migration requiring sign-off
- neither task mentions a typo or a migration. Both functions run on every
real task's live description (main_v2.py:72 feeds the budget charge;
task_executor_v2.py:170/182 feed decide_on_task()'s cannot_execute_alone,
requires_approval(), and should_execute() urgency branch), so this reached
every task submitted without an explicit --hours.

Fixed the same way checkpoint 38 fixed the identical bug shape in
route_task()/infer_required_skills(): match each keyword tier with a
compiled \\b-bounded regex (departments.py's _keyword_pattern()) instead of
substring containment. A task that already contained a real standalone
keyword match is unaffected - only the false positives close.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import os
import shutil
import tempfile

import main_v2
from departments import DepartmentManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class IsolatedCwd:
    def __enter__(self):
        self._original_cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        shutil.copy(os.path.join(REPO_ROOT, "config.json"), self._tmpdir)
        os.chdir(self._tmpdir)
        return self._tmpdir

    def __exit__(self, *exc):
        os.chdir(self._original_cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)


def test_false_positive_substrings_no_longer_match():
    """"typography"/"typology" must not trip the "typo" tier; "immigration"
    must not trip the "migration" tier - for both estimate_hours() and
    estimate_complexity(), which share the same keyword lists."""
    print_section("TEST 1: false-positive substrings no longer match")

    typography_desc = "Update the typography and font sizes across the marketing site"
    immigration_desc = "Review the new immigration policy document for the support team"
    typology_desc = "Draft a typology of our customer segments for the research team"

    hours_typography = DepartmentManager.estimate_hours(typography_desc)
    hours_immigration = DepartmentManager.estimate_hours(immigration_desc)
    complexity_typography = DepartmentManager.estimate_complexity(typography_desc)
    complexity_immigration = DepartmentManager.estimate_complexity(immigration_desc)
    hours_typology = DepartmentManager.estimate_hours(typology_desc)

    print(f"  estimate_hours(typography)      = {hours_typography} (expect 1.0, not 0.5)")
    print(f"  estimate_hours(immigration)     = {hours_immigration} (expect 1.0, not 16.0)")
    print(f"  estimate_hours(typology)        = {hours_typology} (expect 1.0, not 0.5)")
    print(f"  estimate_complexity(typography) = {complexity_typography} (expect 0.5, not 0.2)")
    print(f"  estimate_complexity(immigration)= {complexity_immigration} (expect 0.5, not 0.9)")

    assert hours_typography == 1.0, "typography' falsely tripped the 'typo' LOW_EFFORT tier"
    assert hours_immigration == 1.0, "'immigration' falsely tripped the 'migration' HIGH_EFFORT tier"
    assert hours_typology == 1.0, "'typology' falsely tripped the 'typo' LOW_EFFORT tier"
    assert complexity_typography == 0.5, "'typography' falsely tripped the 'typo' LOW_COMPLEXITY tier"
    assert complexity_immigration == 0.5, "'immigration' falsely tripped the 'migration' HIGH_COMPLEXITY tier"

    print("\n  [OK] no false positives from substring containment")


def test_real_keywords_still_match():
    """The fix must not regress the true positives these tiers exist for -
    a standalone "typo"/"migration"/"refactor"/"rename"/"asap" must still be
    recognized."""
    print_section("TEST 2: real standalone keywords still match")

    cases = [
        (DepartmentManager.estimate_hours, "Fix a typo in the README", 0.5, "typo"),
        (DepartmentManager.estimate_hours, "Plan the database migration to the new platform", 16.0, "migration"),
        (DepartmentManager.estimate_complexity, "Refactor the auth module for clarity", 0.9, "refactor"),
        (DepartmentManager.estimate_complexity, "Rename a variable for clarity", 0.2, "rename"),
        (DepartmentManager.estimate_urgency, "Need this asap, there's an outage in prod", 0.9, "asap/outage"),
        (DepartmentManager.estimate_urgency, "No rush on this, whenever works", 0.2, "no rush/whenever"),
    ]
    for fn, desc, expected, label in cases:
        result = fn(desc)
        print(f"  {fn.__name__}({label!r} case) = {result} (expect {expected})")
        assert result == expected, f"real keyword match for {label!r} regressed: got {result}, expected {expected}"

    print("\n  [OK] all real standalone keyword matches preserved")


def test_multi_word_phrase_keywords_still_match():
    """Multi-word tier entries ("copy change", "no rush", "not urgent") span
    a space; the \\b-bounded pattern must still match them as a phrase."""
    print_section("TEST 3: multi-word phrase keywords still match")

    copy_change_complexity = DepartmentManager.estimate_complexity("Just a copy change on the homepage")
    no_hurry_urgency = DepartmentManager.estimate_urgency("No hurry on this one")

    print(f"  estimate_complexity('copy change' phrase) = {copy_change_complexity} (expect 0.2)")
    print(f"  estimate_urgency('no hurry' phrase)        = {no_hurry_urgency} (expect 0.2)")

    assert copy_change_complexity == 0.2
    assert no_hurry_urgency == 0.2

    print("\n  [OK] multi-word phrase keywords still match as whole phrases")


def test_live_submit_task_uses_fixed_estimate():
    """End-to-end: main_v2.submit_task() (no explicit --hours) must persist
    the corrected estimate for a real task description, through the exact
    production call path (main_v2.py:72)."""
    print_section("TEST 4: live main_v2.submit_task() uses the fixed estimate")

    with IsolatedCwd():
        task = main_v2.submit_task("Update the typography on the marketing site")
        assert task is not None, "submit_task() returned None unexpectedly"

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        persisted = tasks[-1]
        print(f"  persisted estimated_hours = {persisted['estimated_hours']} (expect 1.0, not 0.5)")
        assert persisted["estimated_hours"] == 1.0, (
            "submit_task() still persists the false-positive 'typo' estimate for a typography task"
        )

    print("\n  [OK] real submit_task() path persists the corrected estimate")


if __name__ == "__main__":
    test_false_positive_substrings_no_longer_match()
    test_real_keywords_still_match()
    test_multi_word_phrase_keywords_still_match()
    test_live_submit_task_uses_fixed_estimate()

    print_section("ALL TESTS PASSED")
    print("  [OK] estimate_hours()/estimate_complexity()/estimate_urgency()")
    print("       no longer false-positive on substrings; real keyword and")
    print("       phrase matches, and the live submit_task() path, all hold.")
