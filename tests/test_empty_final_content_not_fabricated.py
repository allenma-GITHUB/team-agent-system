#!/usr/bin/env python3
"""
A real LLM call that genuinely COMPLETES (no exception, no pending tool
request) but whose final turn has empty content must never be reported with
the "<Dept> team analyzed the task and produced a plan" placeholder - that
text is only meant to stand in for a task that never reached a real LLM at
all (self.llm is None). agent_loop.AgentRunResult.completed()'s own
docstring warns callers "not [to] merely check[] that final_content is
non-empty" - final_content CAN legitimately be "" on a completed run, and
pre-fix, task_executor_v2.DepartmentHeadAgent._run_locked()'s
`analysis = run.final_content or analysis` treated that empty string as
falsy and silently substituted the fabricated placeholder, implying a plan
was produced when none was.

This is the same bug family (escalations/incomplete runs reported with
fabricated-sounding output) checkpoints 27, 28, 34, and 59 each closed one
instance of - 59 fixed the except-Exception branch specifically and left
this one, the success-path `or`, untouched. tests/test_llm_exception_escalates.py
only exercises the exception branch; no existing test ever drives a
"completed, empty final_content" or "max_iterations, empty final_content"
run.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent

PLACEHOLDER_FRAGMENT = "analyzed the task and produced a plan"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class EmptyContentCompletedProvider:
    """A provider whose turn completes (no tool calls requested, no
    exception) but whose final text is empty - plausible on some real API
    responses, trivially reachable from a test double."""

    def __init__(self):
        self.calls = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.calls += 1
        return {
            "content": "", "tokens_estimate": 12,
            "provider": "fake", "model": "fake-1", "tool_calls": [],
        }


class EmptyContentStuckProvider:
    """Always requests a tool the registry doesn't have, so the loop runs
    out its iteration cap without ever producing text - final_content is
    "" AND the run never completes."""

    def __init__(self):
        self.calls = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.calls += 1
        return {
            "content": "", "tokens_estimate": 5,
            "provider": "fake", "model": "fake-1",
            "tool_calls": [{"name": "nonexistent_tool", "arguments": {}}],
        }


class RealTextProvider:
    """A normal, successful run with genuine final text - the regression
    guard that the fix must not touch."""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.calls += 1
        return {
            "content": self.text, "tokens_estimate": 20,
            "provider": "fake", "model": "fake-1", "tool_calls": [],
        }


def _agent(provider, suffix, department="engineering"):
    registry = AgentRegistry(data_file=f"data/test_empty_fc_agents_{suffix}.json")
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

    budgets = BudgetManager(data_file=f"data/test_empty_fc_budgets_{suffix}.json")
    budgets.allocate(department, 100000)

    agent = DepartmentHeadAgent(
        department, llm_provider=provider,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_empty_fc_metrics_{suffix}.json"),
    )
    return agent, registry, budgets


def test_completed_run_with_empty_content_is_not_fabricated():
    print_section("1. Completed Run, Empty final_content -> Honest, Not Fabricated")

    provider = EmptyContentCompletedProvider()
    agent, registry, budgets = _agent(provider, "a")

    result = agent.run("Investigate the login outage and report findings.", 2.0)

    print(f"  status           : {result['status']}")
    print(f"  analysis          : {result['analysis']!r}")
    print(f"  quality_score     : {result['quality_score']}")
    print(f"  incomplete_reason : {result.get('incomplete_reason')}")

    assert PLACEHOLDER_FRAGMENT not in result["analysis"], (
        "an empty-but-completed real LLM answer must not be presented as "
        "the mock 'produced a plan' placeholder"
    )
    assert result["analysis"] == "(none: the agent completed with no final answer text)"
    assert result["status"] == "completed"
    assert result.get("incomplete_reason") is None, "a genuinely completed run is not incomplete"
    assert result["quality_score"] == 4.0, "completed with no tool failures still scores clean execution health"
    assert provider.calls == 1


def test_max_iterations_run_with_empty_content_is_not_fabricated():
    print_section("2. Max-Iterations Run, Empty final_content -> Honest Partial Output")

    provider = EmptyContentStuckProvider()
    agent, registry, budgets = _agent(provider, "b")

    result = agent.run("Investigate the login outage and report findings.", 2.0)

    print(f"  status   : {result['status']}")
    print(f"  analysis : {result['analysis']!r}")

    assert result["status"] == "escalated"
    assert PLACEHOLDER_FRAGMENT not in result["analysis"], (
        "the escalation's embedded 'Partial output:' text must not carry "
        "the fabricated mock placeholder either"
    )
    assert "(none: the agent produced no final answer text before stopping)" in result["analysis"]


def test_real_final_content_is_unaffected():
    print_section("3. Regression: A Real, Non-Empty Answer Still Wins")

    real_text = "Root cause: the auth service's token cache was not invalidated after rotation."
    provider = RealTextProvider(real_text)
    agent, registry, budgets = _agent(provider, "c")

    result = agent.run("Investigate the login outage and report findings.", 2.0)

    print(f"  status   : {result['status']}")
    print(f"  analysis : {result['analysis']!r}")

    assert result["analysis"] == real_text
    assert result["status"] == "completed"
    assert result["quality_score"] == 4.0


if __name__ == "__main__":
    test_completed_run_with_empty_content_is_not_fabricated()
    test_max_iterations_run_with_empty_content_is_not_fabricated()
    test_real_final_content_is_unaffected()
    print("\nAll tests passed.")
