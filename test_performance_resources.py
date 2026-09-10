#!/usr/bin/env python3
"""
Test that PerformanceAnalytics.generate_report() surfaces budget and
capacity alongside quality/cost metrics, instead of those living in
three separate reports nobody looks at together.

Uses injected, isolated BudgetManager/CapacityManager/AgentRegistry
instances throughout so this doesn't touch the shared data/ files.
"""
from agent_state import AgentProfile, AgentRegistry
from performance import PerformanceAnalytics
from budgets import BudgetManager, CapacityManager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_resource_summary_reflects_injected_managers():
    """get_resource_summary() should read from whatever managers it's given."""
    print_section("1. Resource Summary Reflects Injected Budget/Capacity")

    budgets = BudgetManager(data_file="data/test_perf_budgets.json")
    budgets.allocate("qa_perf_eng", 20000)
    budgets.request_expense("qa_perf_eng", 5000, category="labor")

    registry = AgentRegistry(data_file="data/test_perf_agents.json")
    registry.register(AgentProfile(
        agent_id="qa_perf_agent", name="Perf Test Agent", agent_type="ManagerAgent",
        department="qa_perf_eng", expertise_areas=["ops"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=4
    ))
    registry.get("qa_perf_agent").current_workload = 3
    capacity = CapacityManager(registry=registry)

    analytics = PerformanceAnalytics(data_file="data/test_perf_metrics.json")
    summary = analytics.get_resource_summary(budgets=budgets, capacity=capacity)

    print(f"  Allocated: ${summary['budget']['total_allocated']:,.2f}")
    print(f"  Spent: ${summary['budget']['total_spent']:,.2f}")
    print(f"  Capacity utilization: {summary['capacity_utilization']:.0%}")

    assert summary["budget"]["total_allocated"] == 20000
    assert summary["budget"]["total_spent"] == 5000
    assert summary["capacity_utilization"] == 0.75  # 3 of 4 slots used


def test_report_includes_resource_section_when_budgets_exist():
    """generate_report() should render the resource overview when there's data."""
    print_section("2. Report Includes Resource Overview")

    budgets = BudgetManager(data_file="data/test_perf_budgets2.json")
    budgets.allocate("qa_perf_over", 1000)
    budgets.request_expense("qa_perf_over", 950, category="labor")  # 95% utilized

    registry = AgentRegistry(data_file="data/test_perf_agents2.json")
    capacity = CapacityManager(registry=registry)

    analytics = PerformanceAnalytics(data_file="data/test_perf_metrics2.json")
    analytics.record_task("qa_perf_agent2", "Perf Agent 2", "qa_perf_over",
                           quality=4.0, duration=2.0, cost=100, success=True)

    report = analytics.generate_report(budgets=budgets, capacity=capacity)
    print(report)

    assert "Resource Overview" in report
    assert "Budget Allocated: $1,000.00" in report
    assert "Budget Spent: $950.00" in report
    assert "Over-Budget Departments: qa_perf_over" in report


def test_report_omits_resource_section_when_nothing_allocated():
    """A fresh system with no budgets shouldn't show an empty resource section."""
    print_section("3. Report Omits Resource Overview With No Budgets Allocated")

    budgets = BudgetManager(data_file="data/test_perf_budgets3.json")  # nothing allocated
    registry = AgentRegistry(data_file="data/test_perf_agents3.json")
    capacity = CapacityManager(registry=registry)

    analytics = PerformanceAnalytics(data_file="data/test_perf_metrics3.json")
    analytics.record_task("qa_perf_agent3", "Perf Agent 3", "qa_perf_none",
                           quality=4.0, duration=1.0, cost=50, success=True)

    report = analytics.generate_report(budgets=budgets, capacity=capacity)
    print(f"  'Resource Overview' in report: {'Resource Overview' in report}")

    assert "Resource Overview" not in report
    assert "System Overview" in report  # the rest of the report still renders


def main():
    print("\n" + "=" * 60)
    print("  PERFORMANCE REPORT + RESOURCE OVERVIEW DEMONSTRATION")
    print("=" * 60)

    test_resource_summary_reflects_injected_managers()
    test_report_includes_resource_section_when_budgets_exist()
    test_report_omits_resource_section_when_nothing_allocated()

    print("\n" + "=" * 60)
    print("  [OK] All performance-resource tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
