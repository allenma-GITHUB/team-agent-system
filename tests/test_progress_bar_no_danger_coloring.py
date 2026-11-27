#!/usr/bin/env python3
"""
Checkpoint 110: static/dashboard.html's workflow-card "Progress" bar reused
pctClass()'s "high = bad" danger scale - the same one budget/capacity/
spend-by-category-share use - for workflows.py's get_progress() (a 0-1
completion fraction, documented in its own docstring as "completion
percentage"). Checkpoint 109 added the highIsGood flag to fix the same bug
shape for trust_score, but explicitly listed "workflow progress" among the
four callers it assumed were correctly using the original "high = bad"
polarity, never checking that assumption. It wasn't: completing a workflow
is good, not dangerous.

Confirmed live by extracting the real pctClass()/bar() source from
dashboard.html and running it under node (see DAILY_PROGRESS.md checkpoint
110 for the full repro/output): pre-fix, a workflow at 95-100% progress
rendered "bad"/red - simultaneously with its own badge.completed CSS class,
which is styled GREEN (var(--good)) - a direct, visible contradiction on the
same card.

The obvious-looking fix (mechanically flip polarity with highIsGood=true,
the same parameter checkpoint 109 added) was checked live and rejected: it
does fix the 95-100% case, but it also makes a freshly-started workflow
(progress 0.0, which is normal - nothing has gone wrong, and the card's own
badge.in_progress class is a neutral blue, not a warning) render "bad"/red
for the first time. That trades one false alarm for another instead of
removing it, because unlike budget/capacity (where approaching the limit is
genuinely risky) or trust_score (where approaching zero genuinely reflects
accumulated failures, and 0.5 is a true neutral midpoint default),
workflow-progress has no "risky end" at all - 0% is just early, 100% is
done, and nothing about the raw fraction signals a problem on its own (the
real problem states - escalated/failed/paused - already have their own
badge colors).

Fixed: pctClass(pct, highIsGood) gained a third mode. highIsGood === null
(as opposed to the default/undefined used by budget/capacity/spend-share, or
true used by trust_score) opts a caller out of the danger scale entirely -
pctClass returns "" unconditionally, so the bar still fills to the correct
width but never takes the "bad"/"warn" class. Only the Progress bar's call
site passes null; every other caller's behavior, including trust_score's
checkpoint-109 fix, is unchanged (see
test_trust_bar_color_polarity.py::test_pre_existing_bar_callers_are_unchanged
for budget/capacity/spend-share and
test_high_is_good_formula_flips_polarity_for_trust_boundary_values for
trust_score - neither needed any change for this checkpoint).

This repo's test suite does not shell out to a JS engine (adding one would
violate "zero third-party dependencies, standard library only, everywhere"
for a test-only dependency), so - consistent with every other
dashboard.html-only checkpoint - this pins the fix via structural/regex
assertions against the real page source, plus a pure-Python mirror of the
exact pinned formula to exercise boundary values the same way the live node
repro did.
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


def _pct_class_mirror(pct: float, high_is_good=False) -> str:
    """Pure-Python mirror of the exact fixed pctClass() formula pinned by
    test_pct_class_null_short_circuits_before_danger_math() below."""
    if high_is_good is None:
        return ""
    danger = (1 - pct) if high_is_good else pct
    if danger >= 0.9:
        return "bad"
    if danger >= 0.7:
        return "warn"
    return ""


def test_pct_class_null_short_circuits_before_danger_math():
    """Pins the exact fixed source: the null-check must come before the
    danger computation, not after (an after-the-fact "" override would still
    be correct for pctClass alone, but the ordering matters for anyone
    reading danger's value, so pin what's actually shipped)."""
    print_section("1. pctClass() Short-Circuits On highIsGood === null")

    source = _dashboard_source()
    pct_class_fn = re.search(
        r"function pctClass\(pct, highIsGood\) \{(.*?)\n\}", source, re.S
    )
    assert pct_class_fn, "pctClass() not found"
    body = pct_class_fn.group(1)

    null_check_pos = body.find("highIsGood === null")
    danger_pos = body.find("const danger")
    assert null_check_pos != -1, "pctClass must check highIsGood === null"
    assert danger_pos != -1, "pctClass must still compute danger for other callers"
    assert null_check_pos < danger_pos, (
        "the null check must short-circuit before the danger computation"
    )
    assert 'return "";' in body[:danger_pos], (
        "highIsGood === null must return the neutral class unconditionally"
    )


def test_progress_bar_passes_null():
    """The one call site this bug concerns: the Progress bar must pass
    highIsGood=null (not true, not omitted), or it goes back to either the
    original backwards polarity or the newly-discovered opposite false
    alarm."""
    print_section("2. The Workflow Card's Progress Bar Passes highIsGood=null")

    source = _dashboard_source()
    assert 'bar("Progress", w.progress, `${(w.progress * 100).toFixed(0)}%`, null)' in source, (
        "Progress bar call must pass null as its 4th argument"
    )
    # Regression guard: must not have silently regressed to the no-4th-arg
    # form (the original bug) or to true (the rejected "just flip it" fix).
    assert 'bar("Progress", w.progress, `${(w.progress * 100).toFixed(0)}%`)' not in source
    assert 'bar("Progress", w.progress, `${(w.progress * 100).toFixed(0)}%`, true)' not in source


def test_progress_never_renders_danger_color_across_the_full_range():
    """Exercises the same boundary values the live node repro used,
    against the pure-Python mirror pinned above - proving the fix actually
    changes behavior (no more "bad" at the high end), without introducing
    the newly-discovered "bad" at the low end that the naive highIsGood=true
    fix would have."""
    print_section("3. Progress Takes No Danger Color At Any Value")

    for pct in [0.00, 0.10, 0.25, 0.50, 0.70, 0.90, 0.95, 1.00]:
        assert _pct_class_mirror(pct, high_is_good=None) == "", (
            f"progress={pct} must never render bad/warn, got "
            f"{_pct_class_mirror(pct, high_is_good=None)!r}"
        )

    # The two concrete regressions this checkpoint specifically guards
    # against, named explicitly:
    assert _pct_class_mirror(1.00, high_is_good=False) == "bad", (
        "sanity check: this is the original bug this checkpoint fixes - "
        "a fully-complete workflow must no longer take this path"
    )
    assert _pct_class_mirror(0.00, high_is_good=True) == "bad", (
        "sanity check: this is what the naive 'just flip it' fix would have "
        "done to a freshly-started workflow - must not be what's shipped"
    )


def test_trust_and_utilization_callers_are_unaffected_by_this_checkpoint():
    """Regression guard scoped to this checkpoint: adding the null branch to
    pctClass must not change behavior for any caller that doesn't pass
    null - trust_score (checkpoint 109) and the three utilization-style
    bars keep their exact existing formula path."""
    print_section("4. Non-Progress Callers Are Untouched By The null Branch")

    assert _pct_class_mirror(1.00, high_is_good=True) == ""      # trust: max trust, neutral
    assert _pct_class_mirror(0.05, high_is_good=True) == "bad"   # trust: near-zero trust, flagged
    assert _pct_class_mirror(0.95, high_is_good=False) == "bad"  # budget: near-limit, flagged
    assert _pct_class_mirror(0.10, high_is_good=False) == ""     # budget: healthy, neutral


def main():
    print("\n" + "=" * 60)
    print("  PROGRESS BAR NO-DANGER-COLORING REGRESSION TEST")
    print("=" * 60)

    test_pct_class_null_short_circuits_before_danger_math()
    test_progress_bar_passes_null()
    test_progress_never_renders_danger_color_across_the_full_range()
    test_trust_and_utilization_callers_are_unaffected_by_this_checkpoint()

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
