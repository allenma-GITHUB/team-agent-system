#!/usr/bin/env python3
"""
Checkpoint 68: ExpenseRecord.category ("labor", "llm_tokens",
"workflow_step") has been logged on every real spend since the budget
system existed - the labor charge in decide_on_task()'s budget check
(task_executor_v2.py), the token charge after an LLM run, a workflow
step's committed reservation (workflows.py) - and every one of those
records is persisted to data/budgets.json and reloaded on every startup.
Nothing ever read expense_log back: organization_summary() and
DepartmentBudget only ever report a department's totals, never what kind
of spend made them up.

Confirmed live before this fix: a BudgetManager fed one labor expense and
one llm_tokens expense for the same department had `expense_log` holding
both records correctly, but `grep -rn "expense_log" --include=*.py .`
turned up zero production readers anywhere (web_server.py, main_v2.py,
performance.py) - only tests ever looked at the list directly.

Fixed: BudgetManager.expense_breakdown() sums already-logged amounts by
category (org-wide or scoped to one department), get_resource_summary()
carries it alongside the existing budget totals (so both
generate_report()'s CLI output and build_report_payload()'s /api/report
pick it up automatically), and generate_report()/dashboard.html each
render a "Spend By Category" section - same bar-chart treatment the
existing "Budget Utilization By Department" section next to it already
gets.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from budgets import BudgetManager
from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_expense_breakdown_sums_by_category_org_wide():
    print_section("1. expense_breakdown() Sums Real Logged Expenses By Category")

    budgets = BudgetManager(data_file="data/test_expense_breakdown1.json")
    budgets.allocate("qa_breakdown_eng", 50000)
    budgets.request_expense("qa_breakdown_eng", 1000, category="labor")
    budgets.request_expense("qa_breakdown_eng", 500, category="labor")
    budgets.request_expense("qa_breakdown_eng", 2.5, category="llm_tokens")

    breakdown = budgets.expense_breakdown()
    print(f"  breakdown: {breakdown}")
    assert breakdown == {"labor": 1500.0, "llm_tokens": 2.5}


def test_expense_breakdown_scopes_to_one_department():
    print_section("2. expense_breakdown() Scoped To One Department Ignores Others")

    budgets = BudgetManager(data_file="data/test_expense_breakdown2.json")
    budgets.allocate("qa_breakdown_a", 50000)
    budgets.allocate("qa_breakdown_b", 50000)
    budgets.request_expense("qa_breakdown_a", 1000, category="labor")
    budgets.request_expense("qa_breakdown_b", 9000, category="labor")

    a_only = budgets.expense_breakdown(department="qa_breakdown_a")
    print(f"  qa_breakdown_a only: {a_only}")
    assert a_only == {"labor": 1000.0}


def test_expense_breakdown_empty_with_no_spend():
    print_section("3. No Expenses Logged -> {} Not A Fabricated Zero Entry")

    budgets = BudgetManager(data_file="data/test_expense_breakdown3.json")
    budgets.allocate("qa_breakdown_fresh", 10000)

    assert budgets.expense_breakdown() == {}
    assert budgets.expense_breakdown(department="qa_breakdown_fresh") == {}


def test_resource_summary_carries_the_breakdown():
    print_section("4. get_resource_summary() Carries expense_breakdown")

    budgets = BudgetManager(data_file="data/test_expense_breakdown4.json")
    budgets.allocate("qa_breakdown_sum", 10000)
    budgets.request_expense("qa_breakdown_sum", 300, category="workflow_step")

    analytics = PerformanceAnalytics(data_file="data/test_expense_breakdown4_metrics.json")
    summary = analytics.get_resource_summary(budgets=budgets)
    print(f"  summary['expense_breakdown']: {summary['expense_breakdown']}")
    assert summary["expense_breakdown"] == {"workflow_step": 300.0}


def test_generate_report_prints_spend_by_category_with_real_numbers():
    print_section("5. generate_report() Prints Spend By Category")

    budgets = BudgetManager(data_file="data/test_expense_breakdown5.json")
    budgets.allocate("qa_breakdown_report", 10000)
    budgets.request_expense("qa_breakdown_report", 750, category="labor")
    budgets.request_expense("qa_breakdown_report", 250, category="llm_tokens")

    analytics = PerformanceAnalytics(data_file="data/test_expense_breakdown5_metrics.json")
    report = analytics.generate_report(budgets=budgets)
    print(report)

    assert "Spend By Category:" in report
    assert "labor" in report and "$750.00" in report
    assert "llm_tokens" in report and "$250.00" in report


def test_sub_dollar_category_total_does_not_round_to_zero():
    """A handful of llm_tokens charges (the $0.00001/token placeholder rate)
    total well under $1 - at the 2-decimal precision the rest of this
    report uses, that would print as "$0.00", indistinguishable from no
    spend at all. main_v2.py's own per-task token-cost line already uses
    5 decimals for exactly this reason (checkpoint 46/47); this pins the
    same honesty for the new org-wide category total."""
    print_section("6. A Sub-$1 Category Total Still Shows As Nonzero")

    budgets = BudgetManager(data_file="data/test_expense_breakdown_subdollar.json")
    budgets.allocate("qa_breakdown_tiny", 10000)
    budgets.request_expense("qa_breakdown_tiny", 0.0024, category="llm_tokens")

    analytics = PerformanceAnalytics(data_file="data/test_expense_breakdown_subdollar_metrics.json")
    report = analytics.generate_report(budgets=budgets)
    print(report)

    assert "$0.00240" in report
    assert "$0.00 (" not in report  # never the misleading rounded-to-zero form


def test_generate_report_omits_spend_by_category_with_no_expenses():
    print_section("6. No Expenses Yet -> No Spend By Category Section")

    budgets = BudgetManager(data_file="data/test_expense_breakdown6.json")
    budgets.allocate("qa_breakdown_none", 10000)  # allocated, never spent

    analytics = PerformanceAnalytics(data_file="data/test_expense_breakdown6_metrics.json")
    report = analytics.generate_report(budgets=budgets)

    assert "Resource Overview" in report  # allocation alone still shows this section
    assert "Spend By Category" not in report


def test_cli_and_dashboard_render_the_new_field():
    """Pins the two consumers added alongside the computation, the same
    way checkpoint 66's tool_call_errors test does for its own field."""
    print_section("7. Both generate_report() And The Dashboard Render expense_breakdown")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    performance_source = open(os.path.join(repo_root, "performance.py")).read()
    assert "expense_breakdown" in performance_source
    assert "Spend By Category" in performance_source

    dashboard_source = open(os.path.join(repo_root, "static", "dashboard.html")).read()
    assert "resources.expense_breakdown" in dashboard_source
    assert "escapeHtml(c)" in dashboard_source, \
        "category name must be escaped like every other dynamic field this dashboard renders"
    assert "moneyPrecise(breakdown[c])" in dashboard_source, \
        "a sub-$1 category total must use the same 5-decimal precision as main_v2.py's report"


def main():
    print("\n" + "=" * 60)
    print("  EXPENSE_BREAKDOWN DEAD-FIELD REGRESSION TEST")
    print("=" * 60)

    test_expense_breakdown_sums_by_category_org_wide()
    test_expense_breakdown_scopes_to_one_department()
    test_expense_breakdown_empty_with_no_spend()
    test_resource_summary_carries_the_breakdown()
    test_generate_report_prints_spend_by_category_with_real_numbers()
    test_sub_dollar_category_total_does_not_round_to_zero()
    test_generate_report_omits_spend_by_category_with_no_expenses()
    test_cli_and_dashboard_render_the_new_field()

    print("\n" + "=" * 60)
    print("  [OK] All expense_breakdown tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_expense_breakdown*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
