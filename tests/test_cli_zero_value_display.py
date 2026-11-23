#!/usr/bin/env python3
"""
main_v2.py's show_task() and workflow_status() CLI commands print a task's
(or workflow step's) tokens_used/quality_score the same way web_server.py's
_task_summary() used to, pre-checkpoint-104: `if result.get('tokens_used'):`/
`if result.get('quality_score'):` - a TRUTHY check, not a presence check.
Checkpoint 104 fixed this exact bug at _task_summary() and documented the
contract explicitly: a task that never executed gets neither key at all,
not a zeroed one. That fix was never swept to these two sibling CLI display
sites, even though both read the identical result-dict shape.

Reachable live the same way checkpoint 104's own repro was:
task_executor_v2.DepartmentHeadAgent._run_locked()'s `except Exception`
branch (checkpoint 59) never advances tokens_used past its 0 initializer
before the exception fires, so `mock_cost = tokens_used * 0.00001 == 0.0` -
a real, correctly-computed value, unconditionally present in the returned
dict, with quality_score set to a real, nonzero 2.0 alongside it. Pre-fix:

  main_v2.py show <id>            -> prints "Quality Score: 2.0" with NO
                                      "Tokens Used:" line at all
  main_v2.py workflow-status <id> -> step detail shows "(quality 2.0)" with
                                      the "($cost)" half of the same line
                                      silently dropped

Both tests below drive the real classes end to end (TaskExecutor /
DepartmentHeadAgent / WorkflowEngine) through an exploding provider, with
no mocking of the CLI functions under test, and capture their real stdout.
"""

import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from core import EventBus
from task_executor_v2 import DepartmentHeadAgent, TaskExecutor
from workflows import WorkflowEngine, create_bug_fix_workflow
import main_v2


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class ExplodingToolProvider:
    """A provider whose tool-calling turn raises before any token is
    counted - the real production path that leaves tokens_used at 0 and
    quality_score at a real, nonzero 2.0 (task_executor_v2.py's `except
    Exception` branch)."""

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        raise RuntimeError("simulated provider outage")


def _registry_and_budget(suffix, departments=("engineering",)):
    registry = AgentRegistry(data_file=f"data/test_cli_zero_agents_{suffix}.json")
    for dept in departments:
        state = registry.register(AgentProfile(
            agent_id=f"{dept}_head", name=f"{dept.title()} Head",
            agent_type="ManagerAgent", department=dept,
            expertise_areas=[dept], skill_level=4,
            capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
        ))
        # register() returns an existing state if a prior run left one on
        # disk; metrics/workload accumulate, so reset them to keep this
        # test's assertions stable run to run.
        state.current_workload = 0
        state.learned_preferences = {}
        state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_cli_zero_budgets_{suffix}.json")
    for dept in departments:
        budgets.allocate(dept, 100000)
    return registry, budgets


def test_show_task_does_not_drop_a_real_zero_token_cost():
    print_section("1. show_task() Keeps a Real, Attempted $0.00 Cost Visible")

    registry, budgets = _registry_and_budget("show", departments=("engineering",))
    agent = DepartmentHeadAgent(
        "engineering", llm_provider=ExplodingToolProvider(),
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_cli_zero_metrics_show.json"),
    )
    result = agent.run("Investigate a flaky CI failure", 2.0)
    print(f"  status: {result['status']}, tokens_used: {result['tokens_used']!r}, "
          f"quality_score: {result['quality_score']!r}")
    assert result["status"] == "escalated"
    assert result["tokens_used"] == 0
    assert "tokens_used" in result
    assert result["quality_score"] == 2.0

    tasks_file = "data/test_cli_zero_tasks.json"
    import json
    task = {"id": "0099", "department": "engineering",
            "description": "Investigate a flaky CI failure",
            "created_at": "2026-01-01T00:00:00", "status": result["status"], "result": result}
    with open(tasks_file, "w") as f:
        json.dump([task], f)

    original_tasks_file = main_v2.TASKS_FILE
    main_v2.TASKS_FILE = __import__("pathlib").Path(tasks_file)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main_v2.show_task("0099")
        printed = buf.getvalue()
    finally:
        main_v2.TASKS_FILE = original_tasks_file
        os.remove(tasks_file)

    print(printed)
    assert "Tokens Used: 0" in printed, (
        "a real, present, computed tokens_used=0 must still print - "
        "pre-fix, the truthy check silently suppressed the entire line"
    )
    assert "Quality Score: 2.0" in printed


def test_workflow_status_does_not_drop_a_real_zero_token_cost():
    print_section("2. workflow_status() Keeps a Real, Attempted $0.00 Cost Visible")

    registry, budgets = _registry_and_budget("wf", departments=("support", "engineering"))
    executor = TaskExecutor(
        ExplodingToolProvider(), bus=EventBus(), agent_state_registry=registry,
        budget_manager=budgets, capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_cli_zero_metrics_wf.json"),
    )
    engine = WorkflowEngine(
        data_file="data/test_cli_zero_workflows.json",
        budget_manager=budgets, agent_state_registry=registry,
    )
    engine.register_template(create_bug_fix_workflow())
    instance = engine.create_instance("bug_fix", {"bug_description": "flaky CI"})
    engine.execute_step(instance.instance_id, "triage", executor)

    step_result = engine.instances[instance.instance_id].step_results["triage"]
    print(f"  status: {step_result['status']}, tokens_used: {step_result['tokens_used']!r}, "
          f"quality_score: {step_result['quality_score']!r}")
    assert step_result["tokens_used"] == 0
    assert "tokens_used" in step_result

    original_engine = main_v2.workflow_engine
    main_v2.workflow_engine = engine
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main_v2.workflow_status(instance.instance_id)
        printed = buf.getvalue()
    finally:
        main_v2.workflow_engine = original_engine

    print(printed)
    step_lines = [l for l in printed.splitlines() if "quality" in l]
    assert step_lines, "expected a step detail line with a quality annotation"
    assert "$0.00000" in step_lines[0], (
        "a real, present, computed tokens_used=0/token_cost=0.0 must still show its "
        "cost half of the detail line - pre-fix, only '(quality 2.0)' printed"
    )


def test_show_task_and_workflow_status_unaffected_for_never_attempted_results():
    print_section("3. Regression: a Genuinely Never-Attempted Result Still Shows Neither Field")

    # Mirrors escalate()'s own never-computed result shape (budget/capacity/
    # decision-engine denial, before any execution) - no tokens_used/
    # quality_score key at all, by construction.
    result = {
        "analysis": "Escalated, not executed: Budget denied: insufficient funds",
        "department": "engineering", "agent_id": "engineering_head",
        "status": "escalated", "approved": False,
    }

    import json
    tasks_file = "data/test_cli_zero_tasks_never.json"
    task = {"id": "0100", "department": "engineering", "description": "x",
            "created_at": "2026-01-01T00:00:00", "status": "escalated", "result": result}
    with open(tasks_file, "w") as f:
        json.dump([task], f)

    original_tasks_file = main_v2.TASKS_FILE
    main_v2.TASKS_FILE = __import__("pathlib").Path(tasks_file)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main_v2.show_task("0100")
        printed = buf.getvalue()
    finally:
        main_v2.TASKS_FILE = original_tasks_file
        os.remove(tasks_file)

    print(printed)
    assert "Tokens Used:" not in printed
    assert "Quality Score:" not in printed


if __name__ == "__main__":
    for _ in range(2):  # idempotent: state is reset explicitly in _registry_and_budget()
        test_show_task_does_not_drop_a_real_zero_token_cost()
        test_workflow_status_does_not_drop_a_real_zero_token_cost()
        test_show_task_and_workflow_status_unaffected_for_never_attempted_results()

    print("\nAll tests passed.")
