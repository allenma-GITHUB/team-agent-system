#!/usr/bin/env python3
"""
DecisionContext.urgency was declared, documented, and populated by every
real caller - but read by nothing.

agent_decisions.py's AgentDecisionEngine.should_execute() has had this
comment on its near-full branch since Phase 1:

    # Neutral: decide based on workload and urgency
    if self.agent.current_workload >= self.agent.profile.max_concurrent_tasks * 0.8:
        return False  # Getting full, delegate if urgent

The comment claims the branch reads urgency; the code never did. Confirmed
live before writing any fix: a near-full agent (workload 4/5) delegated an
otherwise-identical task identically at urgency=0.1 and urgency=0.9 -
`grep -rn "urgency" .` across the whole repo turned up only writers
(task_executor_v2.py's hardcoded 0.5, every test's DecisionContext
construction) and the one field declaration, never a read.

Two fixes, both needed to make this observable outside a test:
1. should_execute()'s near-full branch now checks context.urgency, treating
   >= 0.5 as "urgent enough to delegate" - 0.5 is this field's own
   documented neutral point, so a caller that still hands it the old
   hardcoded default keeps delegating exactly as before.
2. departments.py's new estimate_urgency() (mirroring estimate_complexity())
   gives task_executor_v2.py a real, task-derived urgency instead of that
   permanent 0.5 - otherwise fix 1 alone could never fire for a real task,
   only for a test that builds a DecisionContext by hand.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_decisions import AgentDecisionEngine, DecisionContext
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from departments import (
    DEFAULT_URGENCY,
    HIGH_URGENCY,
    LOW_URGENCY,
    DepartmentManager,
)
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _context(urgency: float) -> DecisionContext:
    return DecisionContext(
        task_id="t", task_type="engineering", required_skills=[],
        complexity=0.3, urgency=urgency, estimated_hours=1.0,
        required_approval_level=1,
    )


def test_estimate_urgency_varies_with_task_wording():
    print_section("1. estimate_urgency() Varies With The Task")

    cases = [
        ("Server outage, fix this ASAP", HIGH_URGENCY),
        ("Update the docs whenever, no rush", LOW_URGENCY),
        ("Review the quarterly report", DEFAULT_URGENCY),
    ]
    for description, expected in cases:
        actual = DepartmentManager.estimate_urgency(description)
        print(f"  \"{description}\" -> {actual}")
        assert actual == expected, f"{description!r}: expected {expected}, got {actual}"

    # The >= 0.5 "urgent enough to delegate" line should_execute() now
    # checks: HIGH must clear it, LOW must not, and DEFAULT must sit
    # exactly on it (so a task with no urgency language reproduces the old,
    # always-hardcoded-0.5 behavior exactly).
    assert HIGH_URGENCY >= 0.5
    assert LOW_URGENCY < 0.5
    assert DEFAULT_URGENCY == 0.5


def test_should_execute_reads_urgency_only_on_near_full_branch():
    print_section("2. should_execute() Reads Urgency - Only When It Matters")

    profile = AgentProfile(
        agent_id="t1", name="Test", agent_type="ManagerAgent", department="engineering",
        expertise_areas=["engineering"], skill_level=3, capabilities=[], constraints=[],
        max_concurrent_tasks=5,
    )
    from agent_state import AgentState
    state = AgentState(profile)
    engine = AgentDecisionEngine(state)

    # Near-full (4/5 = 80%, right at the threshold): urgency now decides.
    state.current_workload = 4
    low = engine.should_execute(_context(LOW_URGENCY))
    default = engine.should_execute(_context(DEFAULT_URGENCY))
    high = engine.should_execute(_context(HIGH_URGENCY))
    print(f"  near-full (4/5): low={low} default={default} high={high}")
    assert low is True, "not urgent and nearly full: no rush, finish it myself"
    assert default is False, "the old hardcoded 0.5 must keep delegating, unchanged"
    assert high is False, "urgent and nearly full: hand off to someone with room"

    # Comfortably available (0/5): urgency must NOT matter - should_execute()
    # only consults it on the near-full branch, by design.
    state.current_workload = 0
    low = engine.should_execute(_context(LOW_URGENCY))
    high = engine.should_execute(_context(HIGH_URGENCY))
    print(f"  available (0/5): low={low} high={high}")
    assert low is True and high is True, "urgency is irrelevant with capacity to spare"


def _registered(registry, agent_id, department, skill_level, max_concurrent_tasks, workload):
    profile = AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(), agent_type="ManagerAgent",
        department=department, expertise_areas=[department], skill_level=skill_level,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=max_concurrent_tasks,
    )
    state = registry.register(profile)
    state.current_workload = workload
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()
    state.relationships = {}
    return state


def test_decide_on_task_uses_real_urgency_end_to_end():
    """A near-full DepartmentHeadAgent, with a real available delegate
    registered alongside it, actually changes decision end to end based on
    nothing but the task's own wording - not a hand-built DecisionContext."""
    print_section("3. decide_on_task() End To End: Wording Changes The Decision")

    registry = AgentRegistry(data_file="data/test_urgency_agents.json")
    _registered(registry, "ops_head", "ops", skill_level=3, max_concurrent_tasks=5, workload=4)
    _registered(registry, "ops2_head", "ops2", skill_level=5, max_concurrent_tasks=5, workload=0)

    budgets = BudgetManager(data_file="data/test_urgency_budgets.json")
    budgets.allocate("ops", 100000)
    budgets.allocate("ops2", 100000)

    agent = DepartmentHeadAgent(
        "ops", llm_provider=LLMProvider(), agent_state_registry=registry,
        budget_manager=budgets, capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_urgency_metrics.json"),
    )
    assert agent.agent_state.current_workload == 4, "must stay the near-full state we set up"

    not_urgent = agent.decide_on_task("Clean up some old comments, whenever, no rush", estimated_hours=1.0)
    print(f"  low-urgency wording      -> {not_urgent['decision']} "
          f"(urgency={not_urgent['context'].urgency})")
    assert not_urgent["context"].urgency == LOW_URGENCY
    assert not_urgent["decision"] == "execute", "no rush: the near-full agent keeps it"

    default_wording = agent.decide_on_task("Review the standard onboarding checklist", estimated_hours=1.0)
    print(f"  no-urgency-keyword task  -> {default_wording['decision']} "
          f"(urgency={default_wording['context'].urgency})")
    assert default_wording["context"].urgency == DEFAULT_URGENCY
    assert default_wording["decision"] == "delegate", "unchanged: this is the old hardcoded behavior"
    assert default_wording["assigned_agent_id"] == "ops2_head"

    urgent = agent.decide_on_task("Production outage, fix this ASAP", estimated_hours=1.0)
    print(f"  high-urgency wording     -> {urgent['decision']} "
          f"(urgency={urgent['context'].urgency})")
    assert urgent["context"].urgency == HIGH_URGENCY
    assert urgent["decision"] == "delegate", "urgent and full: hand off to who has room"
    assert urgent["assigned_agent_id"] == "ops2_head"


if __name__ == "__main__":
    for _ in range(2):  # idempotent: agent state reset explicitly in the helpers above
        test_estimate_urgency_varies_with_task_wording()
        test_should_execute_reads_urgency_only_on_near_full_branch()
        test_decide_on_task_uses_real_urgency_end_to_end()

    print("\n✅ All urgency-wiring tests passed!\n")
