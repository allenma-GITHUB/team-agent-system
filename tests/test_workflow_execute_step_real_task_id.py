#!/usr/bin/env python3
"""
Test that WorkflowEngine.execute_step() passes the workflow instance's own
real id through to a real task_executor_v2.TaskExecutor as task_id, instead
of letting the executor mint its own disconnected synthetic one.

CHECKPOINT 85's own Next Steps named this exact gap and explicitly left it
out of scope: execute_step() called `executor.execute(department,
description)` with no task_id, through the identical buggy path checkpoint
85 fixed for main_v2.py's submitted tasks. Confirmed live before this fix
(see DAILY_PROGRESS.md checkpoint 86) with a real WorkflowEngine + real
TaskExecutor + real BudgetManager: a bug_fix workflow's "triage" step
(no approval, no estimated_cost) produced labor/llm_tokens ExpenseRecords
tagged with a `task_<timestamp>` id that had nothing to do with the
workflow instance that actually requested the work - a human reading
`budget expenses` for that department had no way to connect the charge back
to the workflow instance shown by `workflow status`.

This is a *different* gap from the approval-gated reservation charge
(`approve_step()`'s `confirm_reservation(..., task_id=instance_id)` call,
workflows.py:503) - that call site already passed the real instance_id
correctly and needed no change. Only the labor/llm_tokens charge triggered
by execute_step()'s own `executor.execute()` call had this gap.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib

from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import TaskExecutor
from workflows import WorkflowEngine, create_bug_fix_workflow


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _isolated_setup(suffix: str):
    registry = AgentRegistry(data_file=f"data/test_wetrti_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_wetrti_budgets_{suffix}.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file=f"data/test_wetrti_metrics_{suffix}.json")
    executor = TaskExecutor(
        LLMProvider(), agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    )
    engine = WorkflowEngine(
        data_file=f"data/test_wetrti_workflows_{suffix}.json",
        budget_manager=budgets, agent_state_registry=registry
    )
    engine.register_template(create_bug_fix_workflow())
    return engine, executor, budgets


def _cleanup(suffix: str):
    for f in pathlib.Path("data").glob(f"test_wetrti_*_{suffix}.json"):
        f.unlink(missing_ok=True)


def test_execute_step_tags_real_executor_charges_with_the_instance_id():
    """A no-approval step's labor/llm_tokens ExpenseRecords should carry the
    workflow instance's own id, not a synthetic task_<timestamp> the
    executor invented because nothing told it the real one."""
    print_section("1. execute_step() Passes The Real Instance Id Through To TaskExecutor")

    engine, executor, budgets = _isolated_setup("basic")
    instance = engine.create_instance("bug_fix", {"bug": "repro"})
    engine.start_instance(instance.instance_id)
    step = engine.get_next_step(instance.instance_id)
    assert step.step_id == "triage"
    assert step.requires_approval is False

    ok = engine.execute_step(instance.instance_id, step.step_id, executor)
    print(f"  execute_step() returned: {ok}")
    assert ok

    dept_expenses = [e for e in budgets.expense_log if e.department == step.owner_department]
    print(f"  expenses for {step.owner_department!r}: "
          f"{[(e.category, e.task_id) for e in dept_expenses]}")
    assert len(dept_expenses) >= 2  # labor + llm_tokens
    for expense in dept_expenses:
        assert expense.task_id == instance.instance_id, (
            f"{expense.category} expense carries {expense.task_id!r}, "
            f"not the real instance id {instance.instance_id!r}"
        )
        assert not expense.task_id.startswith("task_"), (
            "still the old disconnected synthetic id, not the workflow's own"
        )

    _cleanup("basic")


def test_two_instances_of_the_same_workflow_keep_their_charges_apart():
    """Two separate instances of the same template must not have their
    executor charges cross-tagged with each other's id - the same
    per-caller-not-shared concern checkpoint 85's own tests pinned for
    main_v2.py's per-task ids."""
    print_section("2. Two Workflow Instances Keep Separate task_ids")

    engine, executor, budgets = _isolated_setup("twoinst")
    instance_a = engine.create_instance("bug_fix", {"bug": "a"})
    instance_b = engine.create_instance("bug_fix", {"bug": "b"})
    engine.start_instance(instance_a.instance_id)
    engine.start_instance(instance_b.instance_id)

    step_a = engine.get_next_step(instance_a.instance_id)
    step_b = engine.get_next_step(instance_b.instance_id)
    engine.execute_step(instance_a.instance_id, step_a.step_id, executor)
    engine.execute_step(instance_b.instance_id, step_b.step_id, executor)

    dept_expenses = [e for e in budgets.expense_log if e.department == step_a.owner_department]
    task_ids = {e.task_id for e in dept_expenses}
    print(f"  distinct task_ids recorded: {task_ids}")
    assert instance_a.instance_id in task_ids
    assert instance_b.instance_id in task_ids
    assert instance_a.instance_id != instance_b.instance_id

    _cleanup("twoinst")


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW execute_step() REAL TASK_ID DEMONSTRATION")
    print("=" * 60)

    test_execute_step_tags_real_executor_charges_with_the_instance_id()
    test_two_instances_of_the_same_workflow_keep_their_charges_apart()

    print("\n" + "=" * 60)
    print("  [OK] All execute_step real-task_id tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
