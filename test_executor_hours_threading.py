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
from budgets import budget_manager


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
    executor = TaskExecutor(LLMProvider(), bus=bus, max_workers=2)
    executor.execute(department, "A five-hour task", estimated_hours=5.0)

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} (5h * $100/hr default = $500 expected)")
    assert budget.spent == 500


def test_execute_parallel_passes_per_task_hours_through():
    """Each task in a parallel batch should be billed for its own hours, not a shared default."""
    print_section("2. execute_parallel() Threads Per-Task estimated_hours Through")

    department = "qa_exec_parallel"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    executor = TaskExecutor(LLMProvider(), bus=bus, max_workers=2)
    executor.execute_parallel([
        (department, "A two-hour task", 2.0),
        (department, "A three-hour task", 3.0),
    ])

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} ((2h + 3h) * $100/hr default = $500 expected)")
    assert budget.spent == 500


def test_execute_parallel_still_accepts_two_tuples():
    """Backward compatibility: a bare (department, description) tuple defaults to 1.0h."""
    print_section("3. execute_parallel() Still Accepts (department, description)")

    department = "qa_exec_default"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    executor = TaskExecutor(LLMProvider(), bus=bus, max_workers=2)
    executor.execute_parallel([(department, "No hours specified")])

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} (1h * $100/hr default = $100 expected)")
    assert budget.spent == 100


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


if __name__ == "__main__":
    main()
