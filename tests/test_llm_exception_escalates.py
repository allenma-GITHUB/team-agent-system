#!/usr/bin/env python3
"""
A provider that raises mid-call (API outage, all models overloaded, a
malformed response - anything _call_anthropic_tools/_call_openai_tools can
raise as ToolCallFailedError, or any other exception a real network call can
throw) must never be reported as a completed, approved task.

Pre-fix, task_executor_v2.DepartmentHeadAgent._run_locked()'s except-Exception
handler around the LLM call set quality_score=2.0 and nothing else - it never
set incomplete_reason, so the method fell through to the "completed"/
"approved": True return with the generic placeholder analysis text ("<Dept>
team analyzed the task and produced a plan.") standing in for output that was
never produced. That is the same bug family as checkpoints 27, 28, and 34
(escalations/incomplete runs recorded as done), just the one instance of it
left open for an outright exception - and, before this file, untested.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class ExplodingToolProvider:
    """Stands in for a real provider whose tool-calling turn raises -
    Anthropic/OpenAI overloaded, a network error, anything generate_with_tools
    can throw rather than return."""

    def __init__(self, error=None):
        self.error = error or RuntimeError("all models unavailable")
        self.calls = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.calls += 1
        raise self.error


class ExplodingSingleShotProvider:
    """Same failure mode on the non-tool-calling path (generate()), covering
    the gemini/groq/nvidia branch of the same try/except."""

    def __init__(self, error=None):
        self.error = error or RuntimeError("provider unreachable")
        self.calls = 0

    def supports_tools(self, task_type="general"):
        return False

    def generate(self, prompt, task_type="general"):
        self.calls += 1
        raise self.error


def _agent(provider, suffix, department="engineering"):
    registry = AgentRegistry(data_file=f"data/test_llm_exc_agents_{suffix}.json")
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

    budgets = BudgetManager(data_file=f"data/test_llm_exc_budgets_{suffix}.json")
    budgets.allocate(department, 100000)

    agent = DepartmentHeadAgent(
        department, llm_provider=provider,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_llm_exc_metrics_{suffix}.json"),
    )
    return agent, registry, budgets


def test_tool_call_exception_is_escalated_not_completed():
    print_section("1. A Provider That Raises Mid-Tool-Call Escalates")

    provider = ExplodingToolProvider(RuntimeError("Anthropic API error: overloaded"))
    agent, registry, budgets = _agent(provider, "a")

    result = agent.run("Fix the critical production bug", 2.0)

    print(f"  status     : {result['status']}")
    print(f"  approved   : {result['approved']}")
    print(f"  analysis   : {result['analysis']}")

    assert result["status"] == "escalated", "a provider exception is not a completed task"
    assert result["approved"] is False
    assert "overloaded" in result["analysis"]
    # The generic placeholder must never be presented as if it were real
    # output - this is specifically what made the pre-fix "Partial output:"
    # text misleading.
    assert "analyzed the task and produced a plan" not in result["analysis"]
    assert provider.calls == 1

    metrics = registry.get("engineering_head").metrics
    assert metrics.error_rate == 1.0, "must count against the agent, not inflate success"
    assert registry.get("engineering_head").current_workload == 0, "workload must still be released"

    spent = budgets.get_budget("engineering").spent
    print(f"  labor budget still charged: ${spent:,.2f}")
    assert spent > 0, "the attempt really consumed staff time and must not be refunded"


def test_single_shot_exception_is_escalated_not_completed():
    print_section("2. A Non-Tool Provider That Raises Also Escalates")

    provider = ExplodingSingleShotProvider(ConnectionError("provider unreachable"))
    agent, registry, _ = _agent(provider, "b")

    result = agent.run("Plan something", 1.0)

    print(f"  status   : {result['status']}")
    print(f"  analysis : {result['analysis']}")

    assert result["status"] == "escalated"
    assert result["approved"] is False
    assert "provider unreachable" in result["analysis"]
    assert provider.calls == 1
    assert registry.get("engineering_head").metrics.error_rate == 1.0


if __name__ == "__main__":
    for _ in range(2):  # idempotent: state is reset explicitly in _agent()
        test_tool_call_exception_is_escalated_not_completed()
        test_single_shot_exception_is_escalated_not_completed()

    print("\n✅ All LLM-exception escalation tests passed!\n")
