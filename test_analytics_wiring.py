#!/usr/bin/env python3
"""
Test that DepartmentHeadAgent.run() actually feeds performance.analytics,
not just agent_state's own per-agent metrics. Before this, PerformanceAnalytics
and its generate_report() (including the resource overview added earlier
today) always showed "Total Tasks: 0" no matter how much work ran, because
nothing in the execution path ever called analytics.record_task().
"""
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent
from budgets import budget_manager
from performance import analytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_completed_task_is_recorded_in_analytics():
    """A normal, completed task should show up in the org-wide analytics."""
    print_section("1. Completed Task Recorded In Analytics")

    department = "qa_analytics_wiring"
    budget_manager.allocate(department, 10000)

    before = analytics.get_agent_metrics(f"{department}_head")
    tasks_before = before.tasks_completed if before else 0

    bus = EventBus()
    agent = DepartmentHeadAgent(department, llm_provider=LLMProvider(), bus=bus, cost_per_hour=100)
    result = agent.run("Investigate a minor UI glitch", estimated_hours=1.0)
    assert result["status"] == "completed"

    after = analytics.get_agent_metrics(f"{department}_head")
    print(f"  Tasks completed before -> after: {tasks_before} -> {after.tasks_completed}")
    assert after is not None
    assert after.tasks_completed == tasks_before + 1
    assert after.department == department


def test_escalated_task_is_not_recorded_in_analytics():
    """A task that never ran (budget denied) shouldn't count as completed work."""
    print_section("2. Escalated Task Is Not Recorded")

    department = "qa_analytics_escalated"
    budget_manager.allocate(department, 10)  # can't afford even a routine task

    bus = EventBus()
    agent = DepartmentHeadAgent(department, llm_provider=LLMProvider(), bus=bus, cost_per_hour=100)
    result = agent.run("Routine task nobody can afford", estimated_hours=1.0)
    assert result["status"] == "escalated"

    metrics = analytics.get_agent_metrics(f"{department}_head")
    print(f"  Metrics for escalated department's agent: {metrics}")
    assert metrics is None


def test_report_reflects_recorded_tasks():
    """generate_report()'s System Overview should include tasks recorded this way."""
    print_section("3. Report Reflects Recorded Tasks")

    department = "qa_analytics_report"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    agent = DepartmentHeadAgent(department, llm_provider=LLMProvider(), bus=bus, cost_per_hour=100)
    agent.run("Task one", estimated_hours=1.0)
    agent.run("Task two", estimated_hours=1.0)

    report = analytics.generate_report()
    metrics = analytics.get_agent_metrics(f"{department}_head")
    print(f"  Agent recorded {metrics.tasks_completed} tasks completed")
    assert metrics.tasks_completed >= 2
    assert "System Overview" in report


def main():
    print("\n" + "=" * 60)
    print("  ANALYTICS WIRING DEMONSTRATION")
    print("=" * 60)

    test_completed_task_is_recorded_in_analytics()
    test_escalated_task_is_not_recorded_in_analytics()
    test_report_reflects_recorded_tasks()

    print("\n" + "=" * 60)
    print("  [OK] All analytics-wiring tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
