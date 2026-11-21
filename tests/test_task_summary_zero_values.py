#!/usr/bin/env python3
"""
web_server._task_summary() pulls `quality_score`/`token_cost` up from a
task's `result` blob to the top level, for the Tasks list view - but
guarded that promotion with `if result.get("field"):`, a TRUTHY check, not
a presence check. Its own docstring states the intended contract: "a task
that never executed (queued, or escalated before execution) gets neither
key, not a zeroed one" - i.e. the real invariant is key ABSENCE, not value
falsiness. Python's `if result.get(...)` treats a genuinely-computed `0.0`
exactly like a missing key, so an attempt that really ran and really cost
(or scored) zero gets silently treated as if it never ran at all.

This is reachable live: task_executor_v2.DepartmentHeadAgent._run_locked()'s
`except Exception` branch (checkpoint 59) never advances `tokens_used` past
its `0` initializer before the exception fires, so `mock_cost = tokens_used
* 0.00001 == 0.0` - a real, correctly-computed value (zero tokens really
were spent, because the call failed before producing one), unconditionally
present in the returned dict (`"token_cost": mock_cost`, no `.get()` guard
on the write side). `_task_summary()` then drops it, making an
attempted-and-escalated task's dashboard row indistinguishable from one that
was declined before any attempt (budget/capacity/decision-engine denial,
whose result dict genuinely never computes these keys at all) - the exact
distinction the function's own docstring says it preserves.

Same bug family, different site, as checkpoints 99 (`learned_preferences`
key mismatch), 100 (`recommended_deadline or 5.0`), and 103
(`run.final_content or analysis`) - a falsy real zero/empty mistaken for
"never computed". `tests/test_web_server.py`'s two related tests only cover
(a) a completed task with real nonzero token_cost, and (b) an
escalated-BEFORE-execution task whose result dict has no token_cost key at
all; neither drives the exception/attempted-then-escalated path through
_task_summary().
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent
from web_server import _task_summary


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class ExplodingToolProvider:
    """A provider whose tool-calling turn raises before any token is
    counted - the real production path that leaves tokens_used at 0 and
    therefore token_cost at a genuine (not placeholder) 0.0."""

    def __init__(self):
        self.calls = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.calls += 1
        raise RuntimeError("simulated provider outage")


def _agent(provider, suffix, department="engineering"):
    registry = AgentRegistry(data_file=f"data/test_summary_zero_agents_{suffix}.json")
    state = registry.register(AgentProfile(
        agent_id=f"{department}_head", name=f"{department.title()} Head",
        agent_type="ManagerAgent", department=department,
        expertise_areas=[department], skill_level=4,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    ))
    state.current_workload = 0
    state.learned_preferences = {}
    # register() returns an existing state if a prior run left one on disk;
    # metrics accumulate, so reset them to keep absolute assertions stable.
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_summary_zero_budgets_{suffix}.json")
    budgets.allocate(department, 100000)

    agent = DepartmentHeadAgent(
        department, llm_provider=provider,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_summary_zero_metrics_{suffix}.json"),
    )
    return agent, registry, budgets


def test_real_zero_token_cost_survives_the_list_view_summary():
    print_section("1. An Attempted-Then-Escalated Task's Real $0.00 Cost Is Not Dropped")

    provider = ExplodingToolProvider()
    agent, registry, budgets = _agent(provider, "a")

    result = agent.run("Investigate a flaky CI failure in the build pipeline", 2.0)

    print(f"  status       : {result['status']}")
    print(f"  tokens_used   : {result['tokens_used']}")
    print(f"  token_cost    : {result['token_cost']}")
    print(f"  quality_score : {result['quality_score']}")

    # The underlying result really did compute these, as real (zero) values.
    assert result["status"] == "escalated"
    assert result["tokens_used"] == 0
    assert result["token_cost"] == 0.0
    assert "token_cost" in result, "the key must be present even though the value is 0.0"

    task = {"id": "zero-cost-task", "department": "engineering",
            "status": result["status"], "result": result}
    summary = _task_summary(task)
    print(f"  summary keys  : {sorted(summary.keys())}")

    assert "token_cost" in summary, (
        "a real, genuinely-computed $0.00 cost must survive the list view - "
        "pre-fix, the truthy check silently dropped it, making an attempted "
        "task indistinguishable from one that was never attempted at all"
    )
    assert summary["token_cost"] == 0.0


def test_a_task_never_attempted_still_has_no_promoted_fields():
    print_section("2. Regression: A Genuinely Never-Attempted Task Still Has Neither Key")

    # Mirrors the escalate() helper's own never-computed result shape
    # (task_executor_v2.py's delegation-cycle/budget-denial/decision-engine
    # paths) - no quality_score/token_cost key at all, by construction.
    result = {
        "analysis": "Escalated, not executed: Budget denied: insufficient funds",
        "department": "engineering", "agent_id": "engineering_head",
        "status": "escalated", "approved": False,
    }
    task = {"id": "never-attempted-task", "department": "engineering",
            "status": "escalated", "result": result}
    summary = _task_summary(task)
    print(f"  summary keys: {sorted(summary.keys())}")

    assert "quality_score" not in summary
    assert "token_cost" not in summary


def test_a_completed_task_with_real_nonzero_values_is_unaffected():
    print_section("3. Regression: Real Nonzero Values Still Promote Exactly As Before")

    result = {
        "analysis": "Root cause identified.", "department": "engineering",
        "status": "completed", "approved": True,
        "quality_score": 4.0, "token_cost": 0.00123,
    }
    task = {"id": "normal-task", "department": "engineering",
            "status": "completed", "result": result}
    summary = _task_summary(task)
    print(f"  summary keys: {sorted(summary.keys())}")

    assert summary["quality_score"] == 4.0
    assert summary["token_cost"] == 0.00123
    assert "result" not in summary


if __name__ == "__main__":
    for _ in range(2):  # idempotent: state is reset explicitly in _agent()
        test_real_zero_token_cost_survives_the_list_view_summary()
        test_a_task_never_attempted_still_has_no_promoted_fields()
        test_a_completed_task_with_real_nonzero_values_is_unaffected()

    print("\nAll tests passed.")
