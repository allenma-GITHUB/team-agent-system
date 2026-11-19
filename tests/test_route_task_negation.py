#!/usr/bin/env python3
"""
Checkpoint 102. DepartmentManager.route_task() scored every department by a
plain count of its config.json keywords found anywhere in the description -
no negation handling at all. Checkpoints 97/98 gave estimate_hours()/
estimate_complexity()/estimate_urgency() exactly this fix (word-boundary
matching, then negation-awareness via _is_negated()/_matches_non_negated()),
but route_task()'s own keyword-scoring loop, right next to them in the same
file, was never given it - it has the identical shape (sum 1 per keyword
found) and the identical blind spot.

Confirmed live before the fix, direct import, real config.json, no mocking:

    route_task("This is not a sales deal, it is a support issue for an "
               "unhappy client")
    -> "sales"

Scoring breakdown that produced it: sales keywords matched "sales", "deal",
and "client" (score 3 - two of them, "sales" and "deal", sit inside the very
clause that explicitly rules sales out); support keywords matched "support"
and "issue" (score 2). The description denies sales and states support, and
was routed to the one department it denies - the real production path
submit_task() (main_v2.py) calls whenever a task is submitted without an
explicit --dept.

Fixed by reusing _is_negated() (already defined above route_task() in
departments.py for the three estimate_*() functions) in the scoring loop:
a department keyword only counts toward that department's score if it has
at least one non-negated match. Same clause-bounded, 4-word-window, literal-
cue-only scope as the existing fix - no change to word-boundary matching,
tie-breaking, or the empty-scores default-department fallback.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from departments import DepartmentManager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_negated_department_keyword_no_longer_wins_routing():
    """The exact live false positive proven above (the first case - the
    other two already routed correctly even before the fix, by sheer
    keyword-count luck, and are pinned here only as same-shape coverage):
    a department explicitly ruled out in the text must not out-score the
    department the text actually names."""
    print_section("TEST 1: a negated department keyword no longer wins routing")

    cases = [
        ("This is not a sales deal, it is a support issue for an unhappy client",
         "support", "not a sales deal ... support issue"),
        ("Not a design request - this is purely a backend bug fix",
         "engineering", "not a design request ... backend bug fix"),
        ("No sales pitch needed here, just help this customer troubleshoot their issue",
         "support", "no sales pitch ... troubleshoot their issue"),
    ]
    for desc, expected, label in cases:
        got = DepartmentManager.route_task(desc)
        print(f"  route_task({label!r}) -> {got!r} (expect {expected!r})")
        assert got == expected, f"{label!r}: routed to {got!r}, expected {expected!r}"

    print("\n  [OK] negated department keywords no longer win over the real topic")


def test_real_unnegated_routing_still_works():
    """The fix must not regress ordinary, un-negated routing - including a
    description that genuinely mentions the same words the cases above
    negate, so this isn't just 'sales/support never route' in disguise."""
    print_section("TEST 2: real, un-negated routing is unaffected")

    cases = [
        ("We need a sales proposal and pitch deck for the new client deal", "sales"),
        ("Fix the backend API bug in production", "engineering"),
        ("Help this customer troubleshoot a support issue", "support"),
        ("Approve budget allocation", "finance"),
    ]
    for desc, expected in cases:
        got = DepartmentManager.route_task(desc)
        print(f"  route_task({desc!r}) -> {got!r} (expect {expected!r})")
        assert got == expected, f"{desc!r}: routed to {got!r}, expected {expected!r}"

    print("\n  [OK] real, un-negated keyword routing is unaffected")


def test_negation_does_not_cross_a_clause_boundary():
    """A negation cue in an earlier clause must not suppress a real,
    un-negated department keyword in a later clause separated by
    punctuation - same clause-boundary rule _is_negated() already enforces
    for the estimate_*() tiers (test_negation_keyword_tiers.py's own
    TEST 5), now exercised through route_task()."""
    print_section("TEST 3: negation is clause-bounded, not sentence-wide")

    desc = "Not a design task, this is clearly a backend database error"
    got = DepartmentManager.route_task(desc)
    print(f"  route_task({desc!r}) -> {got!r} (expect 'engineering', 'not' must not leak past the comma)")
    assert got == "engineering", (
        f"negation leaked across a clause boundary: got {got!r}, expected 'engineering' "
        f"('backend'/'database'/'error' are real, un-negated engineering keywords after the comma)"
    )

    print("\n  [OK] negation does not cross a clause boundary")


def test_negated_keywords_with_no_other_signal_fall_through_to_default():
    """When negation suppresses every department's only match, routing
    must fall back to config.json's default_department - never a
    fabricated department kept alive only by a negated mention."""
    print_section("TEST 4: all-negated description falls through to the default")

    desc = "This is not a design task and not a sales task either"
    got = DepartmentManager.route_task(desc)
    print(f"  route_task({desc!r}) -> {got!r} (expect the configured default_department)")
    config = DepartmentManager.load_config()
    assert got == config.get("default_department", "engineering"), (
        f"got {got!r}, expected the default department with every real match negated"
    )

    print("\n  [OK] fully-negated descriptions fall through to the default department")


if __name__ == "__main__":
    test_negated_department_keyword_no_longer_wins_routing()
    test_real_unnegated_routing_still_works()
    test_negation_does_not_cross_a_clause_boundary()
    test_negated_keywords_with_no_other_signal_fall_through_to_default()

    print_section("ALL TESTS PASSED")
    print("  [OK] route_task() now recognizes literal, local negation of a")
    print("       department's own keywords, without regressing real matches")
    print("       or leaking negation across clause boundaries.")
