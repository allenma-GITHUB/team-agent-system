#!/usr/bin/env python3
"""
Checkpoint 98. DepartmentManager.estimate_hours()/estimate_complexity()/
estimate_urgency() had no negation handling: checkpoint 97's \\b-bounded
keyword patterns correctly reject a keyword that's a substring of a longer
word ("typo" inside "typography"), but a keyword that's a real, standalone
word is matched even when a negation cue sits right in front of it -
"urgent", "critical", "blocking", and "migration" are real standalone words
in "not urgent", "non-critical", "isn't blocking", and "no platform
migration needed" too.

Confirmed live before the fix, direct import, no mocking:
    estimate_urgency("This is not urgent, take your time")                 -> 0.9 (HIGH)
    estimate_urgency("This is a non-critical fix, no rush")                -> 0.9 (HIGH)
    estimate_urgency("This isn't blocking anything, whenever is fine")     -> 0.9 (HIGH)
    estimate_complexity("This is not a major overhaul, just a small tweak")-> 0.9 (HIGH)
    estimate_hours("No platform migration needed here")                   -> 16.0 (HIGH)
A task explicitly described as *not* urgent, *non*-critical, or needing *no*
migration was billed, gated, and routed as if it were exactly that -
reaching the same real call path checkpoint 97 documented (main_v2.py:72's
budget charge; task_executor_v2.py's decide_on_task()). Notably,
LOW_URGENCY_KEYWORDS already lists "not urgent" and "no hurry" as their own
LOW-urgency phrases - they were simply unreachable, because HIGH_URGENCY's
bare "urgent"/"blocking" match ran first and returned before LOW was ever
checked.

Fixed by adding departments.py's _is_negated()/_matches_non_negated():
a clause-bounded (stops at . , ; : ! ?), word-windowed (last 4 words) check
for a literal negation cue ("not", "non", "no", "isn't", "doesn't", ...)
immediately before a tier match. Deliberately narrow, same spirit as
checkpoint 97's own boundary fix: only literal, local negation is handled.
Negation that crosses a clause boundary the cue isn't in, double negation,
and sarcasm are explicitly out of scope (see departments.py's comment above
_NEGATION_WORDS) and are not claimed as fixed here.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from departments import DepartmentManager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_negated_high_tier_no_longer_matches():
    """A negated HIGH-tier keyword must not return the HIGH value - the
    exact live false positives proven above."""
    print_section("TEST 1: negated HIGH-tier keywords no longer trip HIGH")

    cases = [
        (DepartmentManager.estimate_urgency, "This is not urgent, take your time", 0.9, "not urgent"),
        (DepartmentManager.estimate_urgency, "This is a non-critical fix, no rush", 0.9, "non-critical"),
        (DepartmentManager.estimate_urgency, "This isn't blocking anything, whenever is fine", 0.9, "isn't blocking"),
        (DepartmentManager.estimate_complexity, "This is not a major overhaul, just a small tweak", 0.9, "not a major overhaul"),
        (DepartmentManager.estimate_hours, "No platform migration needed here", 16.0, "no platform migration"),
    ]
    for fn, desc, wrong_high_value, label in cases:
        result = fn(desc)
        print(f"  {fn.__name__}({label!r}) = {result} (must not be HIGH={wrong_high_value})")
        assert result != wrong_high_value, f"{label!r} still false-positived into the HIGH tier: {result}"

    print("\n  [OK] negated HIGH-tier matches no longer return the HIGH value")


def test_negated_high_tier_falls_through_to_real_low_phrase():
    """Once HIGH is correctly suppressed, these descriptions resolve to
    LOW - either because a LOW phrase keyword is already present in the
    same text (the tier design checkpoint 97 inherited already has "not
    urgent"/"no hurry"/"no rush" as LOW phrases) or because a LOW keyword
    like "tweak" is the actual signal."""
    print_section("TEST 2: negated HIGH falls through to the real LOW signal")

    cases = [
        (DepartmentManager.estimate_urgency, "This is not urgent, take your time", 0.2, "not urgent -> LOW phrase"),
        (DepartmentManager.estimate_urgency, "This is a non-critical fix, no rush", 0.2, "no rush -> LOW phrase"),
        (DepartmentManager.estimate_urgency, "This isn't blocking anything, whenever is fine", 0.2, "whenever -> LOW phrase"),
        (DepartmentManager.estimate_complexity, "This is not a major overhaul, just a small tweak", 0.2, "tweak -> LOW keyword"),
    ]
    for fn, desc, expected, label in cases:
        result = fn(desc)
        print(f"  {fn.__name__}({label!r}) = {result} (expect {expected})")
        assert result == expected, f"{label!r}: got {result}, expected {expected}"

    print("\n  [OK] LOW signals already present in the text are now reachable")


def test_negated_with_no_other_signal_falls_through_to_default():
    """When negation suppresses the only tier match and no other keyword
    (HIGH or LOW) is present, the result must be the plain DEFAULT -
    never a fabricated tier."""
    print_section("TEST 3: negation with no other signal -> DEFAULT")

    cases = [
        (DepartmentManager.estimate_hours, "No platform migration needed here", 1.0, "no migration -> DEFAULT hours"),
        (DepartmentManager.estimate_hours, "This is not a quick fix, needs real investigation", 1.0, "not a quick fix -> DEFAULT hours"),
    ]
    for fn, desc, expected, label in cases:
        result = fn(desc)
        print(f"  {fn.__name__}({label!r}) = {result} (expect {expected})")
        assert result == expected, f"{label!r}: got {result}, expected {expected}"

    print("\n  [OK] negation with no remaining signal yields DEFAULT, not a guess")


def test_real_unnegated_keywords_still_match():
    """The fix must not regress ordinary, un-negated keyword matches -
    these are the same true positives checkpoint 97 pinned."""
    print_section("TEST 4: real, un-negated keywords are unaffected")

    cases = [
        (DepartmentManager.estimate_urgency, "This is urgent, drop everything", 0.9, "urgent"),
        (DepartmentManager.estimate_hours, "We need a platform migration this week", 16.0, "platform migration"),
        (DepartmentManager.estimate_complexity, "This requires a security overhaul", 0.9, "security overhaul"),
        (DepartmentManager.estimate_hours, "Fix a typo in the footer", 0.5, "typo"),
    ]
    for fn, desc, expected, label in cases:
        result = fn(desc)
        print(f"  {fn.__name__}({label!r}) = {result} (expect {expected})")
        assert result == expected, f"real match for {label!r} regressed: got {result}, expected {expected}"

    print("\n  [OK] un-negated real keyword matches are unaffected")


def test_negation_does_not_cross_a_clause_boundary():
    """A negation cue in an earlier clause must not suppress a real,
    un-negated keyword in a later clause separated by punctuation."""
    print_section("TEST 5: negation is clause-bounded, not sentence-wide")

    desc = "Not sure about scope, but this platform migration is critical"
    hours = DepartmentManager.estimate_hours(desc)
    urgency = DepartmentManager.estimate_urgency(desc)

    print(f"  estimate_hours(cross-clause)   = {hours} (expect 16.0, 'not' must not leak past the comma)")
    print(f"  estimate_urgency(cross-clause) = {urgency} (expect 0.9, same reason)")

    assert hours == 16.0, "negation leaked across a clause boundary and suppressed a real HIGH_EFFORT match"
    assert urgency == 0.9, "negation leaked across a clause boundary and suppressed a real HIGH_URGENCY match"

    print("\n  [OK] negation does not cross a clause boundary")


if __name__ == "__main__":
    test_negated_high_tier_no_longer_matches()
    test_negated_high_tier_falls_through_to_real_low_phrase()
    test_negated_with_no_other_signal_falls_through_to_default()
    test_real_unnegated_keywords_still_match()
    test_negation_does_not_cross_a_clause_boundary()

    print_section("ALL TESTS PASSED")
    print("  [OK] estimate_hours()/estimate_complexity()/estimate_urgency()")
    print("       now recognize literal, local negation of their HIGH/LOW")
    print("       keyword tiers, without regressing real matches or")
    print("       leaking negation across clause boundaries.")
