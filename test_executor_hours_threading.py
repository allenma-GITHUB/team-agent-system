#!/usr/bin/env python3
"""
Test that TaskExecutor actually threads a per-task estimated_hours through to
DepartmentHeadAgent.run(), both sequentially and in parallel. Before this,
execute()/execute_parallel() always used run()'s default of 1.0h regardless
of what a task really represented, so every task's budget cost and recorded
duration were identical no matter how big or small the task actually was.
"""
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import TaskExecutor
from agent_state import AgentProfile, AgentRegistry
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics

# Isolated instances instead of the shared global singletons.
agent_registry = AgentRegistry(data_file="data/test_eh_agents.json")
budget_manager = BudgetManager(data_file="data/test_eh_budgets.json")
capacity_manager = CapacityManager(registry=agent_registry)
analytics = PerformanceAnalytics(data_file="data/test_eh_metrics.json")

# TaskExecutor's on-demand agents get DepartmentHeadAgent's fallback profile
# (ManagerAgent, skill_level 3) with no explicit cost_per_hour, so they bill
# at that profile's derived rate (see agent_state.AgentProfile.hourly_rate())
# rather than a hardcoded number - keeps this test correct if the rate table
# in agent_state.py ever changes.
FALLBACK_RATE = AgentProfile(
    agent_id="rate_probe", name="", agent_type="ManagerAgent", department="x",
    expertise_areas=[], skill_level=3, capabilities=[], constraints=[]
).hourly_rate()


def _executor(bus) -> TaskExecutor:
    return TaskExecutor(
        LLMProvider(), bus=bus, max_workers=2,
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics
    )


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_execute_passes_estimated_hours_through():
    """A single execute() call with a real hours estimate should cost accordingly."""
    print_section("1. execute() Threads estimated_hours Through")

    department = "qa_exec_hours"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    executor = _executor(bus)
    executor.execute(department, "A five-hour task", estimated_hours=5.0)

    budget = budget_manager.get_budget(department)
    expected = 5.0 * FALLBACK_RATE
    print(f"  Spent: ${budget.spent:,.2f} (5h * ${FALLBACK_RATE}/hr fallback rate = ${expected:,.2f} expected)")
    assert budget.spent == expected


def test_execute_parallel_passes_per_task_hours_through():
    """Each task in a parallel batch should be billed for its own hours, not a shared default."""
    print_section("2. execute_parallel() Threads Per-Task estimated_hours Through")

    department = "qa_exec_parallel"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    executor = _executor(bus)
    executor.execute_parallel([
        (department, "A two-hour task", 2.0),
        (department, "A three-hour task", 3.0),
    ])

    budget = budget_manager.get_budget(department)
    expected = (2.0 + 3.0) * FALLBACK_RATE
    print(f"  Spent: ${budget.spent:,.2f} ((2h + 3h) * ${FALLBACK_RATE}/hr fallback rate = ${expected:,.2f} expected)")
    assert budget.spent == expected


def test_execute_parallel_still_accepts_two_tuples():
    """Backward compatibility: a bare (department, description) tuple defaults to 1.0h."""
    print_section("3. execute_parallel() Still Accepts (department, description)")

    department = "qa_exec_default"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    executor = _executor(bus)
    executor.execute_parallel([(department, "No hours specified")])

    budget = budget_manager.get_budget(department)
    expected = 1.0 * FALLBACK_RATE
    print(f"  Spent: ${budget.spent:,.2f} (1h * ${FALLBACK_RATE}/hr fallback rate = ${expected:,.2f} expected)")
    assert budget.spent == expected


def main():
    print("\n" + "=" * 60)
    print("  EXECUTOR ESTIMATED-HOURS THREADING DEMONSTRATION")
    print("=" * 60)

    test_execute_passes_estimated_hours_through()
    test_execute_parallel_passes_per_task_hours_through()
    test_execute_parallel_still_accepts_two_tuples()

    print("\n" + "=" * 60)
    print("  [OK] All executor-hours-threading tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_eh_agents.json", "data/test_eh_budgets.json", "data/test_eh_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
