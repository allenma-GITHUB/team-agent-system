#!/usr/bin/env python3
"""
Test that TaskExecutor.execute_parallel() doesn't lose updates when
multiple tasks route to the SAME department in the same parallel batch.

Real bug found by manual review: TaskExecutor caches one DepartmentHeadAgent
per department and reuses it across worker threads, so N tasks to the same
department call run() on the SAME agent object concurrently. Every mutation
inside run() (workload increment/decrement, budget spend, performance
metrics) was a non-atomic read-modify-write on state shared by every
thread - no lock. A concrete repro (30 concurrent same-department tasks,
8 workers) recorded as few as 28 completions and correspondingly short
budget spend, with zero errors reported to the caller - the tasks
themselves succeeded, their bookkeeping silently vanished.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import TaskExecutor
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _isolated_executor(suffix: str, max_workers: int) -> TaskExecutor:
    registry = AgentRegistry(data_file=f"data/test_csd_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_csd_budgets_{suffix}.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file=f"data/test_csd_metrics_{suffix}.json")
    return TaskExecutor(
        LLMProvider(), max_workers=max_workers,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    ), registry, budgets, analytics


def test_no_lost_updates_across_many_concurrent_same_department_tasks():
    """N tasks to one department, processed concurrently, should record
    exactly N completions and exactly N * rate spent - not fewer."""
    print_section("1. No Lost Updates Across Concurrent Same-Department Tasks")

    department = "race_dept"
    executor, registry, budgets, analytics = _isolated_executor("main", max_workers=8)
    budgets.allocate(department, 1_000_000)

    n = 30
    tasks = [(department, f"task {i}", 1.0) for i in range(n)]
    results = executor.execute_parallel(tasks)

    errors = [r for r in results if "error" in r]
    print(f"  Errors: {len(errors)}")
    assert errors == []

    metrics = analytics.get_agent_metrics(f"{department}_head")
    agent_state = registry.get(f"{department}_head")
    budget = budgets.get_budget(department)
    rate = agent_state.profile.hourly_rate()

    print(f"  tasks_completed: {metrics.tasks_completed} (expected {n})")
    print(f"  workload: {agent_state.current_workload} (expected 0)")
    print(f"  spent: ${budget.spent:,.2f} (expected ${n * rate:,.2f})")

    assert metrics.tasks_completed == n
    assert agent_state.current_workload == 0
    assert budget.spent == n * rate


def test_repeated_runs_stay_correct():
    """Run the same stress scenario several times - a race that only shows
    up occasionally would still be a real bug."""
    print_section("2. Repeated Runs Stay Correct (Not Just Lucky Once)")

    department = "race_dept_repeat"
    for run_num in range(3):
        executor, registry, budgets, analytics = _isolated_executor(f"repeat{run_num}", max_workers=8)
        budgets.allocate(department, 1_000_000)

        n = 20
        tasks = [(department, f"task {i}", 1.0) for i in range(n)]
        executor.execute_parallel(tasks)

        metrics = analytics.get_agent_metrics(f"{department}_head")
        print(f"  Run {run_num + 1}: tasks_completed = {metrics.tasks_completed} (expected {n})")
        assert metrics.tasks_completed == n


def test_different_departments_still_run_concurrently():
    """The fix must not turn execute_parallel() into fully sequential
    execution - different departments should still overlap in wall time."""
    print_section("3. Different Departments Still Run Concurrently")

    import time
    executor, registry, budgets, analytics = _isolated_executor("multi", max_workers=4)
    for dept in ["dept_a", "dept_b", "dept_c", "dept_d"]:
        budgets.allocate(dept, 100000)

    tasks = [(f"dept_{c}", f"task in {c}", 1.0) for c in "abcd"]
    start = time.time()
    executor.execute_parallel(tasks)
    elapsed = time.time() - start

    print(f"  4 different-department tasks completed in {elapsed:.3f}s (mock LLM, should be well under 1s)")
    assert elapsed < 2.0  # sanity bound - not a strict timing assertion, just "didn't serialize badly"


def main():
    print("\n" + "=" * 60)
    print("  CONCURRENT SAME-DEPARTMENT EXECUTION REGRESSION TEST")
    print("=" * 60)

    test_no_lost_updates_across_many_concurrent_same_department_tasks()
    test_repeated_runs_stay_correct()
    test_different_departments_still_run_concurrently()

    print("\n" + "=" * 60)
    print("  [OK] All concurrent-same-department tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_csd_*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
