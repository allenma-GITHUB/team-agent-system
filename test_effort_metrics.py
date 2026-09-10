#!/usr/bin/env python3
"""
Test that recorded task duration reflects business effort (estimated_hours),
not the wall-clock time of a mock LLM call. Before this fix, every task's
recorded duration was near-zero regardless of its estimated_hours, which made
avg_turnaround_time and every duration-based recommendation in performance.py
(workload_rebalance, skill_gap) permanently dead - they could never trigger.
"""
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics

# Isolated instances instead of the shared global singletons.
agent_registry = AgentRegistry(data_file="data/test_em_agents.json")
budget_manager = BudgetManager(data_file="data/test_em_budgets.json")
capacity_manager = CapacityManager(registry=agent_registry)
analytics = PerformanceAnalytics(data_file="data/test_em_metrics.json")


def _agent(department: str, **kwargs) -> DepartmentHeadAgent:
    return DepartmentHeadAgent(
        department, llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics, **kwargs
    )


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_recorded_duration_matches_estimated_hours_not_wall_clock():
    """A task estimated at 6 hours should record ~6h, not milliseconds."""
    print_section("1. Recorded Duration Reflects Business Effort")

    department = "qa_effort_metrics"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    agent = _agent(department, bus=bus, cost_per_hour=50)
    agent.run("A task that represents six hours of real work", estimated_hours=6.0)

    metrics = analytics.get_agent_metrics(f"{department}_head")
    print(f"  avg_duration_hours: {metrics.avg_duration_hours}")
    assert metrics.avg_duration_hours == 6.0  # not ~0.000001h of actual mock call time

    wall_clock_event = bus.get_traces("agent_complete")[0].data["duration"]
    print(f"  Actual wall-clock duration of the mock call: {wall_clock_event:.4f}s (still tracked separately)")
    assert wall_clock_event < 1.0  # the mock LLM call really is near-instant


def test_workload_rebalance_recommendation_can_actually_fire():
    """With real effort hours recorded, a consistently heavy agent should
    trigger the workload_rebalance recommendation - previously impossible
    since avg_duration_hours never exceeded a fraction of a second."""
    print_section("2. Workload Recommendations Can Actually Trigger")

    department = "qa_effort_heavy"
    budget_manager.allocate(department, 100000)

    bus = EventBus()
    agent = _agent(department, bus=bus, cost_per_hour=50)
    for _ in range(3):
        agent.run("A consistently heavy task", estimated_hours=12.0)

    metrics = analytics.get_agent_metrics(f"{department}_head")
    print(f"  avg_duration_hours after 3 twelve-hour tasks: {metrics.avg_duration_hours}")
    assert metrics.avg_duration_hours > 8

    recs = analytics.get_recommendations()
    flagged = [r for r in recs if r["agent"] == metrics.name and r["type"] == "workload_rebalance"]
    print(f"  workload_rebalance recommendations for this agent: {len(flagged)}")
    assert len(flagged) == 1


def main():
    print("\n" + "=" * 60)
    print("  EFFORT-BASED DURATION METRICS DEMONSTRATION")
    print("=" * 60)

    test_recorded_duration_matches_estimated_hours_not_wall_clock()
    test_workload_rebalance_recommendation_can_actually_fire()

    print("\n" + "=" * 60)
    print("  [OK] All effort-metrics tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_em_agents.json", "data/test_em_budgets.json", "data/test_em_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
