#!/usr/bin/env python3
"""
Checkpoint 109: static/dashboard.html's bar()/pctClass() helpers color a bar
by how "full" it is - pctClass(pct) returned "bad" (red) at >= 0.9 and "warn"
(orange) at >= 0.7, on the assumption that a higher pct is always worse. That
holds for every caller pctClass had when the Agent Trust & Collaboration
panel was added (budget utilization, capacity utilization, spend-by-category
share, workflow progress) - but that panel passes rel.trust_score straight
into the same bar(), and agent_state.py's RelationshipScore defines
trust_score as 0-1 where HIGHER is better (more trusted), moved toward 1.0 by
real successful delegations and toward 0.0 by real failed ones
(update_trust_with_agent()). Reusing pctClass() unchanged inverted the
polarity: a maximally-trusted relationship (trust_score 1.0, ten real
collaborations) rendered with the same red "bad" class the dashboard uses for
a department at 100% of its budget, while a relationship trust had nearly
abandoned (trust_score 0.05, a real failed delegation) rendered with no
warning class at all - the same neutral blue as a healthy, under-70%-utilized
budget bar. Confirmed live by extracting the real pctClass()/bar() source
from dashboard.html and running it under node against representative
RelationshipScore values; backwards in both directions (see DAILY_PROGRESS.md
checkpoint 109 for the exact repro and output). This repo's test suite does
not itself shell out to a JS engine - that would add a non-stdlib runtime
dependency to "zero third-party dependencies, standard library only,
everywhere" - so this file pins the fix the same way every other
dashboard.html checkpoint has: structural/regex assertions against the real
page source, plus a pure-Python mirror of the exact two-line formula those
assertions pin, to also exercise the boundary values the live node repro used.

Fixed: pctClass(pct, highIsGood) and bar(label, pct, right, highIsGood) take
an optional highIsGood flag. When set, the "danger" value color is computed
from (1 - pct) instead of pct, so the LOW end of the scale is what crosses
the bad/warn thresholds - without changing what percentage the bar visually
fills to. Only the collaboration panel's call site passes
highIsGood=true; the four pre-existing callers (budget, capacity,
spend-by-category share, workflow progress) call bar()/pctClass() with no

NOTE (later checkpoint): this entry's framing of "workflow progress" as one
of the four pre-existing, presumptively-correct callers turned out to be
wrong - a later checkpoint found it needed a third polarity (no danger
coloring at all, highIsGood=null), not the original 3-arg call. See
test_progress_bar_no_danger_coloring.py; test 3 below was updated to drop
progress from its unchanged-callers list accordingly.

4th argument and are therefore byte-for-byte unchanged in behavior.
"""

import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _dashboard_source() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(repo_root, "static", "dashboard.html")).read()


def _pct_class_mirror(pct: float, high_is_good: bool = False) -> str:
    """Pure-Python mirror of the exact pctClass() formula pinned by
    test_pct_class_and_bar_take_a_high_is_good_flag() below - used only to
    exercise boundary values in this suite without invoking a JS engine."""
    danger = (1 - pct) if high_is_good else pct
    if danger >= 0.9:
        return "bad"
    if danger >= 0.7:
        return "warn"
    return ""


def test_pct_class_and_bar_take_a_high_is_good_flag():
    """Pins the exact fixed source: pctClass/bar both gained the parameter,
    and the danger value is only inverted when it's passed truthy."""
    print_section("1. pctClass()/bar() Accept An Optional highIsGood Flag")

    source = _dashboard_source()

    assert "function pctClass(pct, highIsGood)" in source
    assert "const danger = highIsGood ? 1 - pct : pct;" in source
    assert "function bar(label, pct, right, highIsGood)" in source
    assert "pctClass(clamped, highIsGood)" in source


def test_collaboration_panel_passes_high_is_good():
    """The one call site this bug concerns: the trust-score bar must pass
    highIsGood=true, or a highly-trusted relationship goes back to rendering
    red."""
    print_section("2. The Collaboration Panel's Trust Bar Passes highIsGood=true")

    source = _dashboard_source()
    collab_block = re.search(
        r'\$\("#collaboration-list"\)\.innerHTML = collaboration\.map\(rel =>\n(.*?)\)\.join',
        source, re.S,
    )
    assert collab_block, "collaboration-list rendering block not found"
    assert "rel.trust_score" in collab_block.group(1)
    assert re.search(r",\s*/\*\s*highIsGood\s*\*/\s*true\)", collab_block.group(1)), (
        "trust bar call must pass a truthy highIsGood as its 4th argument"
    )


def test_pre_existing_bar_callers_are_unchanged():
    """Regression guard: the three callers this bug didn't involve (budget,
    capacity, spend-by-category share) must still call bar() with exactly 3
    arguments - confirming this fix is additive-only and none of them
    silently started inverting too.

    Workflow progress is deliberately NOT in this list: a later checkpoint
    (see test_progress_bar_no_danger_coloring.py) found it needed its own
    fix - this checkpoint's note that "workflow progress" was a pre-existing,
    unexamined caller turned out to assume its pre-fix polarity was correct
    when it wasn't."""
    print_section("3. Budget/Capacity/Spend-Share Bars Are Byte-Identical")

    source = _dashboard_source()
    unchanged_calls = [
        'bar("Budget", d.budget.utilization_pct, `${money(d.budget.spent)} / ${money(d.budget.allocated)}`)',
        'bar("Capacity", d.capacity.utilization_pct, `${d.capacity.workload}/${d.capacity.total} tasks`)',
    ]
    for call in unchanged_calls:
        assert call in source, f"expected unchanged 3-arg call site: {call!r}"

    spend_share_call = re.search(
        r"\.map\(c => bar\(escapeHtml\(c\), totalSpend > 0 \? breakdown\[c\] / totalSpend : 0,\n\s*breakdown\[c\] >= 1 \? money\(breakdown\[c\]\) : moneyPrecise\(breakdown\[c\]\)\)\)",
        source,
    )
    assert spend_share_call, "spend-by-category bar call must remain a 3-arg call"


def test_high_is_good_formula_flips_polarity_for_trust_boundary_values():
    """Exercises the same boundary values the live node repro used
    (checkpoint 109's DAILY_PROGRESS.md entry), against the pure-Python
    mirror of the pinned formula above - proving the fix actually changes
    behavior in the right direction, not just that new text exists."""
    print_section("4. highIsGood Flips Which End Of The Scale Is 'Danger'")

    # Before this fix, _pct_class_mirror with high_is_good=False (the old,
    # only behavior) was what trust_score ran through - reproducing the bug.
    assert _pct_class_mirror(1.00, high_is_good=False) == "bad"      # the bug: max trust -> red
    assert _pct_class_mirror(0.05, high_is_good=False) == ""         # the bug: near-zero trust -> neutral

    # After this fix, the collaboration panel passes high_is_good=True.
    assert _pct_class_mirror(1.00, high_is_good=True) == "", \
        "maximally-trusted relationship must not render as a warning"
    assert _pct_class_mirror(0.50, high_is_good=True) == "", \
        "the default never-collaborated trust_score must not render as a warning"
    assert _pct_class_mirror(0.05, high_is_good=True) == "bad", \
        "a relationship trust has nearly abandoned must render as the warning color"
    assert _pct_class_mirror(0.25, high_is_good=True) == "warn"

    # The four pre-existing, utilization-style callers never pass
    # highIsGood, so their long-standing "high = bad" behavior is identical
    # before and after this fix.
    assert _pct_class_mirror(0.95) == _pct_class_mirror(0.95, high_is_good=False) == "bad"
    assert _pct_class_mirror(0.10) == _pct_class_mirror(0.10, high_is_good=False) == ""


def main():
    print("\n" + "=" * 60)
    print("  TRUST BAR COLOR POLARITY REGRESSION TEST")
    print("=" * 60)

    test_pct_class_and_bar_take_a_high_is_good_flag()
    test_collaboration_panel_passes_high_is_good()
    test_pre_existing_bar_callers_are_unchanged()
    test_high_is_good_formula_flips_polarity_for_trust_boundary_values()

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
