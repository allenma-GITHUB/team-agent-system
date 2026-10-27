#!/usr/bin/env python3
"""
BudgetManager.rollover_log has recorded a real PeriodRollover (department,
period, what it spent, what unused allocation was forfeited) every time
_roll_if_due() fires since checkpoint 76, and checkpoint 78 gave it a pull
read surface (BudgetManager.rollover_history(), the `budget history` CLI
command, GET /api/budget-rollovers) - but nothing pushed the signal to
anyone. over_budget_departments() already flags a department straining
against its allocation and reaches get_recommendations(); the opposite
failure mode - a department that just closed a period having left most of
its allocation unspent - had no equivalent. An operator could only catch it
by going to look at `budget history`, the same gap shape rollover_history()
itself closed for rollover_log one level up.

Confirmed live before fixing: allocate a department $10,000/day, spend only
$500, backdate period_start so the period is overdue, then call
get_recommendations() - the rollover_log entry existed (rollover_history()
could already see it) but get_recommendations() had no `budgets` parameter
at all and never looked at it; recs contained nothing about the forfeited
$9,500.

Fixed: get_recommendations() now accepts a `budgets` argument (same
optional-injection shape as get_resource_summary()/generate_report()) and
flags the department's most recent rollover when it forfeited at least half
of that period's known total (spent_in_period + unused_forfeited - the
department's *current* `allocated` is not used, since it can have changed
since the reported period actually ran). Only the latest rollover per
department is considered so an old, already-acted-on rollover doesn't
re-flag forever.

This also exposed a pre-existing bug: generate_report()'s "Recommendations"
section printed `rec['agent']` unconditionally - true of every
recommendation type that existed before this one, but this new
department-keyed type has no "agent" key, so printing it crashed with
KeyError('agent'). Fixed alongside it with the same `agent or department`
fallback static/dashboard.html's loadReport() already uses to merge
agent-keyed and department-keyed recommendation lists.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime
import pathlib

from budgets import BudgetManager
from performance import PerformanceAnalytics

BUDGETS_FILE = "data/test_rollover_rec_budgets.json"
METRICS_FILE = "data/test_rollover_rec_metrics.json"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _backdated_budgets(department: str, allocated: float, spent: float, period: str = "daily") -> BudgetManager:
    pathlib.Path(BUDGETS_FILE).unlink(missing_ok=True)
    bm = BudgetManager(data_file=BUDGETS_FILE)
    budget = bm.allocate(department, allocated, period=period)
    if spent:
        bm.request_expense(department, spent, category="labor", description="repro")
    budget.period_start = (datetime.datetime.utcnow() - datetime.timedelta(days=5)).isoformat()
    bm.save()
    return bm


def test_large_forfeiture_is_recommended():
    print_section("1. A Rollover That Forfeited Most Of Its Period Is Flagged")

    bm = _backdated_budgets("engineering", 10000, 500)  # 95% forfeited
    analytics = PerformanceAnalytics(data_file=METRICS_FILE)

    recs = analytics.get_recommendations(budgets=bm)
    print(f"  recommendations: {recs}")
    flagged = [r for r in recs if r["type"] == "rollover_underspend" and r["department"] == "engineering"]
    assert len(flagged) == 1
    assert "95%" in flagged[0]["issue"]
    assert "$9,500.00" in flagged[0]["issue"]


def test_small_forfeiture_is_not_recommended():
    print_section("2. A Mostly-Spent Period Stays Quiet")

    bm = _backdated_budgets("marketing", 10000, 7000)  # 30% forfeited, below threshold
    analytics = PerformanceAnalytics(data_file=METRICS_FILE)

    recs = analytics.get_recommendations(budgets=bm)
    flagged = [r for r in recs if r["type"] == "rollover_underspend"]
    assert flagged == []


def test_no_budgets_injected_defaults_without_crashing():
    print_section("3. Omitting `budgets` Still Works (Defaults To The Shared Singleton)")

    analytics = PerformanceAnalytics(data_file=METRICS_FILE)
    recs = analytics.get_recommendations()  # no budgets kwarg at all
    print(f"  recommendations: {recs}")  # just needs to not raise


def test_only_the_latest_rollover_per_department_is_flagged():
    print_section("4. An Old Rollover Does Not Re-Flag Forever")

    bm = _backdated_budgets("engineering", 10000, 500)  # first rollover: 95% forfeited
    bm.rollover_history()  # drive the first rollover now

    # Second period: spend heavily, so the *latest* rollover should NOT flag.
    budget = bm.get_budget("engineering")
    bm.request_expense("engineering", 9000, category="labor", description="busy period")
    budget.period_start = (datetime.datetime.utcnow() - datetime.timedelta(days=5)).isoformat()
    bm.save()

    history = bm.rollover_history("engineering")
    print(f"  rollover_history length: {len(history)}")
    assert len(history) == 2  # both rollovers recorded...

    analytics = PerformanceAnalytics(data_file=METRICS_FILE)
    recs = analytics.get_recommendations(budgets=bm)
    flagged = [r for r in recs if r["type"] == "rollover_underspend" and r["department"] == "engineering"]
    print(f"  flagged: {flagged}")
    assert flagged == []  # ...but only the latest (90% spent) is judged


def test_rollover_recommendation_reaches_the_cli_report_text_without_crashing():
    print_section("5. generate_report() Prints It Instead Of Crashing On KeyError('agent')")

    bm = _backdated_budgets("engineering", 10000, 500)
    analytics = PerformanceAnalytics(data_file=METRICS_FILE)

    report = analytics.generate_report(budgets=bm)
    print(report)
    assert "engineering: Reconsider engineering's next allocation" in report


def main():
    print("\n" + "=" * 60)
    print("  ROLLOVER UNDERSPEND RECOMMENDATION TEST")
    print("=" * 60)

    test_large_forfeiture_is_recommended()
    test_small_forfeiture_is_not_recommended()
    test_no_budgets_injected_defaults_without_crashing()
    test_only_the_latest_rollover_per_department_is_flagged()
    test_rollover_recommendation_reaches_the_cli_report_text_without_crashing()

    print("\n" + "=" * 60)
    print("  [OK] All rollover recommendation tests passed!")
    print("=" * 60 + "\n")

    for f in [BUDGETS_FILE, METRICS_FILE]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
