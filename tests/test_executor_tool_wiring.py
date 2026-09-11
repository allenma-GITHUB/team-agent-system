#!/usr/bin/env python3
"""
Test that the ordinary task pipeline (submit -> process) actually uses the
tool-calling loop, and that an agent which runs out of iterations is not
recorded as having done the work.

Checkpoint 34 built the loop but left it reachable only through the
side-door `agent-run` command; every real task still went through a single
completion with no tools. This covers wiring it into DepartmentHeadAgent,
which is the path budget, metrics, task status and workflows all flow
through - hence its own test file.

The load-bearing test is test_incomplete_run_is_escalated_not_completed.
Reporting an unfinished agent run as a completed task would be the FOURTH
instance of this codebase's signature bug (checkpoints 27, 28, and the
escalated-with-a-cheerful-summary case). The loop exposes stopped_reason
precisely so this layer can tell the difference; this test proves it does.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import AGENT_MAX_ITERATIONS, DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class AlwaysWantsToolsProvider:
    """A provider that never stops asking for tools - the exhaustion case."""

    def __init__(self, tool_name="get_department_budget", arguments=None):
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {"department": "engineering"}
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        return {
            "content": "still thinking",
            "tool_calls": [{"id": f"c{self.turns}", "name": self.tool_name,
                            "arguments": self.arguments}],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 7,
        }


class ScriptedToolProvider:
    """Requests one tool, then answers."""

    def __init__(self, tool_name="get_department_budget", arguments=None):
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {"department": "engineering"}
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        if self.turns == 1:
            return {
                "content": "", "tool_calls": [
                    {"id": "c1", "name": self.tool_name, "arguments": self.arguments}],
                "provider": "fake", "model": "FakeModel", "tokens_estimate": 20,
            }
        return {
            "content": "Plan: proceed within budget.", "tool_calls": [],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 5,
        }


class NoToolSupportProvider:
    """Stands in for gemini/groq/nvidia, which this system can't tool-call."""

    def __init__(self):
        self.generate_calls = 0

    def supports_tools(self, task_type="general"):
        return False

    def generate(self, prompt, task_type="general"):
        self.generate_calls += 1
        return {"content": "Single-shot plan.", "provider": "groq",
                "model": "FakeGroq", "tokens_estimate": 11}


def _agent(provider, suffix, department="engineering"):
    registry = AgentRegistry(data_file=f"data/test_wiring_agents_{suffix}.json")
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

    budgets = BudgetManager(data_file=f"data/test_wiring_budgets_{suffix}.json")
    budgets.allocate(department, 100000)

    agent = DepartmentHeadAgent(
        department, llm_provider=provider,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_wiring_metrics_{suffix}.json"),
    )
    return agent, registry, budgets


def test_ordinary_task_now_uses_tools():
    print_section("1. A Normal Task Goes Through The Tool Loop")

    provider = ScriptedToolProvider()
    agent, registry, budgets = _agent(provider, "a")

    result = agent.run("Plan the login fix", 2.0)

    print(f"  status      : {result['status']}")
    print(f"  used_tools  : {result['used_tools']}")
    print(f"  tool_calls  : {result['tool_calls']} (failed: {result['tool_calls_failed']})")
    print(f"  tokens_used : {result['tokens_used']}")
    print(f"  analysis    : {result['analysis']}")

    assert result["status"] == "completed"
    assert result["used_tools"] is True
    assert result["tool_calls"] == 1
    assert result["tool_calls_failed"] == 0
    assert result["tokens_used"] == 25, "tokens must sum across the whole loop, not just the last turn"
    assert result["analysis"] == "Plan: proceed within budget."
    assert provider.turns == 2
    assert budgets.get_budget("engineering").spent > 0
    assert registry.get("engineering_head").metrics.tasks_completed == 1


def test_incomplete_run_is_escalated_not_completed():
    """The load-bearing test - see this module's docstring."""
    print_section("2. Running Out Of Iterations Escalates, It Does Not 'Complete'")

    provider = AlwaysWantsToolsProvider()
    agent, registry, budgets = _agent(provider, "b")

    result = agent.run("Task the agent never finishes", 2.0)

    print(f"  status       : {result['status']}")
    print(f"  quality_score: {result['quality_score']}")
    print(f"  analysis     : {result['analysis'][:90]}")

    assert result["status"] == "escalated", "an unfinished agent run is not a completed task"
    assert result["approved"] is False
    assert "iteration limit" in result["analysis"]
    assert provider.turns == AGENT_MAX_ITERATIONS, "the loop must be bounded"

    # Recorded as a failure, so it drags the agent's success rate down rather
    # than silently inflating it.
    metrics = registry.get("engineering_head").metrics
    print(f"  tasks_completed={metrics.tasks_completed} error_rate={metrics.error_rate}")
    assert metrics.error_rate == 1.0

    # Workload is released even on the unfinished path - otherwise the agent
    # would leak a slot per failure and eventually look permanently busy.
    assert registry.get("engineering_head").current_workload == 0

    # The attempt really did consume tokens and staff time, so the budget is
    # charged and NOT refunded. Reporting the spend alongside an unfinished
    # task is the accurate pair; refunding would understate real cost.
    spent = budgets.get_budget("engineering").spent
    print(f"  budget still charged: ${spent:,.2f}")
    assert spent > 0


def test_provider_without_tool_support_falls_back_cleanly():
    print_section("3. A Non-Tool Provider Degrades To One Completion")

    provider = NoToolSupportProvider()
    agent, _, budgets = _agent(provider, "c")

    result = agent.run("Plan something", 1.0)

    print(f"  status     : {result['status']}")
    print(f"  used_tools : {result['used_tools']}")
    print(f"  analysis   : {result['analysis']}")

    assert result["status"] == "completed"
    assert result["used_tools"] is False, "must not imply a lookup that never happened"
    assert result["tool_calls"] == 0
    assert result["analysis"] == "Single-shot plan."
    assert provider.generate_calls == 1
    assert budgets.get_budget("engineering").spent > 0


def test_failed_tool_calls_lower_the_execution_health_score():
    """quality_score was a hardcoded 4.0 for "the call didn't throw". It now
    varies with whether the agent's tool use actually worked."""
    print_section("4. Tool Failures Lower quality_score")

    healthy, _, _ = _agent(ScriptedToolProvider(), "d")
    healthy_result = healthy.run("Fine task", 1.0)

    # A tool name that doesn't exist - the registry reports it, the loop feeds
    # it back, the model answers anyway.
    broken, _, _ = _agent(ScriptedToolProvider(tool_name="not_a_real_tool", arguments={}), "e")
    broken_result = broken.run("Task with a bad tool call", 1.0)

    print(f"  all tools ok    -> quality_score={healthy_result['quality_score']} "
          f"failed={healthy_result['tool_calls_failed']}")
    print(f"  tool call failed -> quality_score={broken_result['quality_score']} "
          f"failed={broken_result['tool_calls_failed']}")

    assert healthy_result["quality_score"] == 4.0
    assert broken_result["quality_score"] == 3.0
    assert broken_result["tool_calls_failed"] == 1
    # Still a completed task: the agent recovered and answered.
    assert broken_result["status"] == "completed"


def test_agent_tools_read_the_agents_own_injected_managers():
    """An agent built with isolated managers must look up THOSE budgets, not
    the global singletons - otherwise a test would silently read real data."""
    print_section("5. Tools Resolve Against The Agent's Own Managers")

    provider = ScriptedToolProvider(arguments={"department": "engineering"})
    agent, _, budgets = _agent(provider, "f")
    budgets.allocate("engineering", 4242)

    result = agent.run("Check the budget", 1.0)
    assert result["tool_calls_failed"] == 0

    looked_up = agent._tool_registry.execute("get_department_budget", {"department": "engineering"})
    print(f"  tool sees: {looked_up.content}")
    assert "4242" in looked_up.content, "the tool must read the injected manager"


if __name__ == "__main__":
    for _ in range(2):  # idempotent: state is reset explicitly in _agent()
        test_ordinary_task_now_uses_tools()
        test_incomplete_run_is_escalated_not_completed()
        test_provider_without_tool_support_falls_back_cleanly()
        test_failed_tool_calls_lower_the_execution_health_score()
        test_agent_tools_read_the_agents_own_injected_managers()

    print("\n✅ All executor tool-wiring tests passed!\n")
