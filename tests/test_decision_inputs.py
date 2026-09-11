#!/usr/bin/env python3
"""
Test that a task's complexity and approval level are read from the task,
not hardcoded - and that the thresholds they have to clear still line up.

Every task used to be handed `complexity=0.5` and `required_approval_level=2`
by task_executor_v2.decide_on_task(). Both constants sat on the safe side of
every threshold agent_decisions.py tests them against, so for EVERY task ever
submitted:
  - the "cannot_execute_alone" constraint (needs complexity > 0.7) was dead,
    even though config.json gives it to engineering_head and design_head;
  - requires_approval()'s complexity > 0.8 trigger was dead;
  - its required_approval_level > skill_level trigger was dead (2 is below
    every department head's skill level of 3-5).
Approval was reachable only through estimated_hours > 16 - a number typed by
hand at the CLI. A platform migration and a typo fix were equally complex,
forever.

test_tier_values_clear_the_thresholds_they_must_clear is the one to keep:
the coupling between these constants and the comparisons in
agent_decisions.py is implicit, so lowering HIGH_COMPLEXITY to 0.75 would
silently switch the approval trigger back off with every other test still
green.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from departments import (
    DEFAULT_APPROVAL_LEVEL,
    DEFAULT_COMPLEXITY,
    HIGH_APPROVAL_LEVEL,
    HIGH_COMPLEXITY,
    LOW_APPROVAL_LEVEL,
    LOW_COMPLEXITY,
    DepartmentManager,
)
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent

# The comparisons in agent_decisions.py that these tiers exist to reach.
CONSTRAINT_THRESHOLD = 0.7   # can_execute(): "cannot_execute_alone"
APPROVAL_THRESHOLD = 0.8     # requires_approval()


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_complexity_is_read_from_the_task():
    print_section("1. Complexity Varies With The Task")

    cases = [
        ("Full platform migration", HIGH_COMPLEXITY),
        ("Rewrite the auth module", HIGH_COMPLEXITY),
        ("Security compliance review", HIGH_COMPLEXITY),
        ("Fix a typo in the footer", LOW_COMPLEXITY),
        ("Quick copy change", LOW_COMPLEXITY),
        ("Investigate a failing login", DEFAULT_COMPLEXITY),
    ]
    for description, expected in cases:
        actual = DepartmentManager.estimate_complexity(description)
        print(f"  {description:34} -> {actual}")
        assert actual == expected, description

    # Case-insensitive, like estimate_hours().
    assert DepartmentManager.estimate_complexity("FULL PLATFORM MIGRATION") == HIGH_COMPLEXITY


def test_tier_values_clear_the_thresholds_they_must_clear():
    """The load-bearing invariant - see this module's docstring."""
    print_section("2. Tier Values Actually Cross The Decision Thresholds")

    print(f"  HIGH={HIGH_COMPLEXITY} must exceed both "
          f"{CONSTRAINT_THRESHOLD} (constraint) and {APPROVAL_THRESHOLD} (approval)")
    assert HIGH_COMPLEXITY > CONSTRAINT_THRESHOLD
    assert HIGH_COMPLEXITY > APPROVAL_THRESHOLD

    print(f"  DEFAULT={DEFAULT_COMPLEXITY} must cross neither (ordinary work is ungated)")
    assert DEFAULT_COMPLEXITY <= CONSTRAINT_THRESHOLD
    assert DEFAULT_COMPLEXITY <= APPROVAL_THRESHOLD

    print(f"  LOW={LOW_COMPLEXITY} must cross neither")
    assert LOW_COMPLEXITY <= CONSTRAINT_THRESHOLD
    assert LOW_COMPLEXITY <= APPROVAL_THRESHOLD

    print(f"  approval levels: low={LOW_APPROVAL_LEVEL} "
          f"default={DEFAULT_APPROVAL_LEVEL} high={HIGH_APPROVAL_LEVEL}")
    assert DepartmentManager.approval_level_for_complexity(HIGH_COMPLEXITY) == HIGH_APPROVAL_LEVEL
    assert DepartmentManager.approval_level_for_complexity(DEFAULT_COMPLEXITY) == DEFAULT_APPROVAL_LEVEL
    assert DepartmentManager.approval_level_for_complexity(LOW_COMPLEXITY) == LOW_APPROVAL_LEVEL


def _agent(department: str, suffix: str):
    registry = AgentRegistry(data_file=f"data/test_dinput_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_dinput_budgets_{suffix}.json")
    budgets.allocate(department, 500000)
    agent = DepartmentHeadAgent(
        department, llm_provider=LLMProvider(),
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_dinput_metrics_{suffix}.json"),
    )
    state = registry.get(agent.agent_id)
    state.current_workload = 0
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()
    return agent, budgets


def test_cannot_execute_alone_constraint_finally_fires():
    """config.json gives engineering_head this constraint. It has never once
    applied, because complexity never exceeded 0.7."""
    print_section("3. 'cannot_execute_alone' Applies To Complex Work")

    agent, _ = _agent("engineering", "a")
    assert "cannot_execute_alone" in agent.agent_state.profile.constraints

    complex_result = agent.run("Full platform migration", 8.0)
    print(f"  complex task -> {complex_result['status']}: {complex_result['analysis'][:80]}")
    assert complex_result["status"] == "escalated"
    assert "too complex to execute alone" in complex_result["analysis"]

    simple_result = agent.run("Fix a typo in the footer", 1.0)
    print(f"  simple task  -> {simple_result['status']}")
    assert simple_result["status"] == "completed", "ordinary work must be unaffected"


def test_approval_now_triggers_on_complexity_not_only_on_hours():
    """Before this, a 2-hour task could never require approval whatever it
    was, because only estimated_hours > 16 could trigger it."""
    print_section("4. Approval Triggers On Complexity, Not Only On Hours")

    agent, _ = _agent("research", "b")  # research_head has no blocking constraints
    assert agent.agent_state.profile.constraints == []

    decision = agent.decide_on_task("Security compliance migration", estimated_hours=2.0)
    print(f"  2h complex task  -> approval_required={decision['requires_approval']}")
    assert decision["requires_approval"] is True

    easy = agent.decide_on_task("Fix a typo in the footer", estimated_hours=2.0)
    print(f"  2h simple task   -> approval_required={easy['requires_approval']}")
    assert easy["requires_approval"] is False

    # The pre-existing hours trigger still works.
    long_easy = agent.decide_on_task("Fix a typo in the footer", estimated_hours=40.0)
    print(f"  40h simple task  -> approval_required={long_easy['requires_approval']}")
    assert long_easy["requires_approval"] is True


def test_complex_work_still_runs_where_no_constraint_blocks_it():
    """Complexity must gate approval, not silently fail every large task."""
    print_section("5. Complex Work Still Executes Where Nothing Blocks It")

    agent, budgets = _agent("research", "c")
    result = agent.run("Security compliance migration", 3.0)

    print(f"  status={result['status']} spent=${budgets.get_budget('research').spent:,.2f}")
    assert result["status"] == "completed"
    assert budgets.get_budget("research").spent > 0


if __name__ == "__main__":
    for _ in range(2):  # idempotent: agent state reset explicitly in _agent()
        test_complexity_is_read_from_the_task()
        test_tier_values_clear_the_thresholds_they_must_clear()
        test_cannot_execute_alone_constraint_finally_fires()
        test_approval_now_triggers_on_complexity_not_only_on_hours()
        test_complex_work_still_runs_where_no_constraint_blocks_it()

    print("\n✅ All decision-input tests passed!\n")
