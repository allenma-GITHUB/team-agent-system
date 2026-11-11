#!/usr/bin/env python3
"""
An escalated-after-attempting task's metrics/capacity/staffing were computed
and persisted, then dropped from the one dict a human (or main_v2.py's CLI,
or the dashboard) ever sees.

DepartmentHeadAgent._run_locked() has two very different kinds of
"escalated" return. The early ones (decision-engine escalation, budget
denial) happen BEFORE any work is attempted - no LLM call, no
record_performance(), no metrics to show, and main_v2.py's own comment
above its metrics/staffing_capacity print blocks says exactly that: "an
escalated-before-execution result... never had these fields to begin with,
only a completed or iteration-exhausted run does." That's intentional and
untouched by this fix - test 3 below pins it stays that way.

The incomplete_reason return (max-iterations exhaustion, or the LLM call
raising) is different: by the time it executes, self.agent_state.add_task(),
self.agent_state.record_performance(), and self.analytics.record_task() have
ALL already run unconditionally, and capacity/over_capacity/staff_count were
all computed earlier in the same method call - the attempt really happened
and really got recorded. But the dict this branch returned carried none of
"metrics"/"staffing_capacity"/"staff_contributions" out, even though the
sibling "completed" return a few lines below computes the identical three
keys from the identical already-in-scope values. Confirmed live, pre-fix,
direct import, no mocking: an exploding provider left
registry.get(agent_id).metrics.tasks_completed genuinely incremented to 1
while result.get('metrics') was None - main_v2.py's
`if result.get('metrics'): ...` print block is dead code on this path only
because the key never arrives, not because there is nothing to print.

This pins the fix: incomplete_reason's return now carries the same three
keys, built from the same expressions, as the completed return - for both
ways incomplete_reason gets set (max-iteration exhaustion, and an exception
raised by the LLM call).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


class ExplodingProvider:
    """Raises on the first call - the LLM-call-threw branch."""

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        raise RuntimeError("provider outage")

    def generate(self, prompt, task_type="general"):
        raise RuntimeError("provider outage")


class AlwaysWantsToolsProvider:
    """Never stops asking for tools - the iteration-exhaustion branch,
    duplicated from test_tool_call_errors_surfaced.py per this project's
    no-cross-test-imports convention."""

    def __init__(self, tool_name="get_department_budget"):
        self.tool_name = tool_name
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        return {
            "content": "still thinking",
            "tool_calls": [{"id": f"c{self.turns}", "name": self.tool_name,
                             "arguments": {"department": "engineering"}}],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 7,
        }


def _agent(provider, suffix, department="engineering", budget=100000):
    registry = AgentRegistry(data_file=f"data/test_wagmv_agents_{suffix}.json")
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

    budgets = BudgetManager(data_file=f"data/test_wagmv_budgets_{suffix}.json")
    budgets.allocate(department, budget)

    capacity_manager = CapacityManager(registry=registry)
    agent = DepartmentHeadAgent(
        department, llm_provider=provider,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity_manager,
        analytics=PerformanceAnalytics(data_file=f"data/test_wagmv_metrics_{suffix}.json"),
    )
    return agent, registry, budgets, capacity_manager


def _assert_real_fields(result, registry, agent_id, capacity_manager, department):
    assert "metrics" in result, "metrics was computed and recorded; it must be returned too"
    assert "staffing_capacity" in result
    assert "staff_contributions" in result

    state = registry.get(agent_id)
    assert state.metrics.tasks_completed == 1, \
        "record_performance() ran before this return - the registry really was updated"
    assert result["metrics"]["tasks_completed"] == state.metrics.tasks_completed
    assert result["metrics"]["avg_quality"] == state.metrics.avg_quality_score
    assert result["metrics"]["error_rate"] == state.metrics.error_rate

    capacity = capacity_manager.snapshot(department)
    assert result["staffing_capacity"]["agent_count"] == capacity.agent_count
    assert result["staffing_capacity"]["utilization_pct"] == capacity.utilization_pct()
    assert isinstance(result["staff_contributions"], list)


def test_exception_path_carries_metrics_and_capacity():
    print_section("1. An Exploding Provider Still Surfaces Real Metrics/Capacity")

    agent, registry, _, capacity_manager = _agent(ExplodingProvider(), "a")
    result = agent.run("Do something", 1.0)

    print(f"  status            : {result['status']}")
    print(f"  metrics            : {result.get('metrics')}")
    print(f"  staffing_capacity  : {result.get('staffing_capacity')}")
    print(f"  staff_contributions: {len(result.get('staff_contributions', []))} entries")

    assert result["status"] == "escalated"
    assert result["approved"] is False
    _assert_real_fields(result, registry, "engineering_head", capacity_manager, "engineering")


def test_max_iterations_path_carries_metrics_and_capacity():
    print_section("2. Iteration-Exhaustion Escalation Still Surfaces Real Metrics/Capacity")

    agent, registry, _, capacity_manager = _agent(AlwaysWantsToolsProvider(), "b")
    result = agent.run("Task the agent never finishes", 1.0)

    print(f"  status  : {result['status']}")
    print(f"  metrics : {result.get('metrics')}")

    assert result["status"] == "escalated"
    _assert_real_fields(result, registry, "engineering_head", capacity_manager, "engineering")


def test_early_escalation_still_has_no_metrics_fields():
    """The decision-engine/budget-denial escalations happen before any
    attempt - they must NOT gain these fields, since there is no real
    metrics/capacity reading to show for a task that was never run."""
    print_section("3. Escalated-Before-Execution Still Carries None Of These Fields")

    agent, registry, _, capacity_manager = _agent(
        AlwaysWantsToolsProvider(), "c", department="solo", budget=0,
    )
    result = agent.run("No budget to cover this at all", 1.0)

    print(f"  status     : {result['status']}")
    print(f"  result keys: {sorted(result.keys())}")

    assert result["status"] == "escalated"
    state = registry.get("solo_head")
    assert state.metrics.tasks_completed == 0, "this task never reached the LLM at all"
    for absent in ("metrics", "staffing_capacity", "staff_contributions"):
        assert absent not in result, \
            f"{absent} must stay absent for a task that was never attempted"


def test_cli_already_renders_these_fields_once_present():
    """main_v2.py's metrics/staffing_capacity print blocks already exist and
    are gated only on the keys being present - this pins that the fix needed
    no new consumer code, just the missing keys on this one producer path."""
    print_section("4. main_v2.py's Existing Print Blocks Are The Right Consumer")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cli_source = open(os.path.join(repo_root, "main_v2.py")).read()
    assert "result.get('metrics')" in cli_source
    assert "result.get('staffing_capacity')" in cli_source


if __name__ == "__main__":
    for _ in range(2):  # idempotent: state is reset explicitly in _agent()
        test_exception_path_carries_metrics_and_capacity()
        test_max_iterations_path_carries_metrics_and_capacity()
        test_early_escalation_still_has_no_metrics_fields()
        test_cli_already_renders_these_fields_once_present()

    print("\n✅ All incomplete-result metrics/capacity visibility tests passed!\n")
