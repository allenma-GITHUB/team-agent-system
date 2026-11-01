#!/usr/bin/env python3
"""
Test that TaskExecutor actually threads a per-task estimated_hours through to
DepartmentHeadAgent.run(), both sequentially and in parallel. Before this,
execute()/execute_parallel() always used run()'s default of 1.0h regardless
of what a task really represented, so every task's budget cost and recorded
duration were identical no matter how big or small the task actually was.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def _token_cost(department: str) -> float:
    """execute()'s result carries its own token_cost field now (see
    task_executor_v2.py's token_budget_check), but reading the real charge
    back from the budget manager's own expense log is still the simplest way
    to total it across however many tasks a given test ran."""
    return sum(e.amount for e in budget_manager.expense_log
               if e.department == department and e.category == "llm_tokens")


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
    # Labor (hours * rate) plus the real token cost of the LLM call - see
    # task_executor_v2.py's token_budget_check.
    token_cost = _token_cost(department)
    expected = 5.0 * FALLBACK_RATE + token_cost
    print(f"  Spent: ${budget.spent:,.2f} (5h * ${FALLBACK_RATE}/hr fallback rate + "
          f"${token_cost:.5f} token cost = ${expected:,.5f} expected)")
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
    token_cost = _token_cost(department)
    expected = (2.0 + 3.0) * FALLBACK_RATE + token_cost
    print(f"  Spent: ${budget.spent:,.2f} ((2h + 3h) * ${FALLBACK_RATE}/hr fallback rate + "
          f"${token_cost:.5f} token cost = ${expected:,.5f} expected)")
    # Tolerance, not exact equality: these two tasks share a department and
    # run concurrently, so their budget.spent additions land in whatever
    # order the threads acquire the lock in - float addition isn't
    # associative, so the total can differ at the ULP level from a re-sum
    # in a fixed order even though it's still the right total.
    assert abs(budget.spent - expected) < 1e-6


def test_execute_parallel_still_accepts_two_tuples():
    """Backward compatibility: a bare (department, description) tuple defaults to 1.0h."""
    print_section("3. execute_parallel() Still Accepts (department, description)")

    department = "qa_exec_default"
    budget_manager.allocate(department, 10000)

    bus = EventBus()
    executor = _executor(bus)
    executor.execute_parallel([(department, "No hours specified")])

    budget = budget_manager.get_budget(department)
    token_cost = _token_cost(department)
    expected = 1.0 * FALLBACK_RATE + token_cost
    print(f"  Spent: ${budget.spent:,.2f} (1h * ${FALLBACK_RATE}/hr fallback rate + "
          f"${token_cost:.5f} token cost = ${expected:,.5f} expected)")
    assert budget.spent == expected


def test_labor_expense_carries_real_task_id():
    """decide_and_budget() generates a real task_id (decide_on_task()'s
    DecisionContext.task_id) before it ever charges the department, but
    neither labor-charging branch passed it to budgets.py: the
    approval-required branch called approve_decision() (which didn't even
    accept a task_id parameter), and the routine branch's request_expense()
    call named every other field except task_id. Every labor ExpenseRecord
    this executor ever produced had task_id=None, same shape as the
    "computed, never wired through" gap checkpoints 45-83 each closed, just
    on the write side instead of a missing read.

    Confirmed live before the fix: both a routine task (routed through
    request_expense() directly) and a large/urgent one (routed through
    approve_decision(), which requires approval) logged task_id=None.

    Covers both branches, since they're two separate call sites that both
    needed the same fix."""
    print_section("4. Labor Expenses Carry Their Real task_id")

    bus = EventBus()
    executor = _executor(bus)

    routine_dept = "qa_exec_task_id_routine"
    budget_manager.allocate(routine_dept, 10000)
    executor.execute(routine_dept, "A routine task, no approval needed", estimated_hours=1.0)
    routine_labor = [e for e in budget_manager.expense_log
                     if e.department == routine_dept and e.category == "labor"]
    print(f"  routine task_id: {[e.task_id for e in routine_labor]}")
    assert len(routine_labor) == 1
    assert routine_labor[0].task_id is not None
    assert routine_labor[0].task_id.startswith("task_")

    approval_dept = "qa_exec_task_id_approval"
    budget_manager.allocate(approval_dept, 10000)
    executor.execute(
        approval_dept,
        "A huge urgent cross-department strategic restructuring negotiation",
        estimated_hours=40.0,
    )
    approval_labor = [e for e in budget_manager.expense_log
                      if e.department == approval_dept and e.category == "labor"]
    print(f"  approval-required task_id: {[e.task_id for e in approval_labor]}")
    assert len(approval_labor) == 1
    assert approval_labor[0].task_id is not None
    assert approval_labor[0].task_id.startswith("task_")


def main():
    print("\n" + "=" * 60)
    print("  EXECUTOR ESTIMATED-HOURS THREADING DEMONSTRATION")
    print("=" * 60)

    test_execute_passes_estimated_hours_through()
    test_execute_parallel_passes_per_task_hours_through()
    test_execute_parallel_still_accepts_two_tuples()
    test_labor_expense_carries_real_task_id()

    print("\n" + "=" * 60)
    print("  [OK] All executor-hours-threading tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_eh_agents.json", "data/test_eh_budgets.json", "data/test_eh_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
