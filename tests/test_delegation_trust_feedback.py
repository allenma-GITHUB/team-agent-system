#!/usr/bin/env python3
"""
A real delegation must move trust_score, not just read it.

agent_decisions.py's AgentDecisionEngine.find_best_delegate() has ranked
candidates on `trust_score * 0.05` since Phase 1 (see rank_candidate()'s
comment "5. Trust score with delegator"), and agent_state.py's
update_trust_with_agent() is the only method that can ever move a
RelationshipScore off its hardcoded 0.5 default. But nothing in production -
not task_executor_v2.py, not anywhere else - ever called it: grepping the
whole repo (outside this file's own prior test coverage of the method in
isolation) turned up zero production callers. `relationships` stayed `{}`
forever, no matter how many real delegations an agent ran or how they
turned out, so the trust term in rank_candidate() was a dead weight - the
same value for every candidate, always, because nothing ever fed it a real
outcome.

Confirmed live before writing the fix: five consecutive real, successful
delegations through TaskExecutor between two real DepartmentHeadAgents left
`engineering_head.relationships` an empty dict throughout.

The fix teaches task_executor_v2.py's _dispatch_delegation() - the one
place in the codebase that actually sees how a real delegation came back -
to call update_trust_with_agent() on the outcome, using the same
completed-and-quality>=3.0 bar _run_locked() already uses for its own
learn_preference() self-affinity update, just applied to the delegate
instead of the delegator.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import TaskExecutor


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _profile(agent_id: str, department: str, skill: int = 5) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=["shared"], skill_level=skill,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    )


def _make_executor(departments, disliked=(), budget_amount=100000, suffix="a"):
    """Mirrors tests/test_delegation.py's helper of the same name - same
    reset-on-register discipline, since register() returns an *existing*
    state and metrics/relationships accumulate across runs otherwise."""
    registry = AgentRegistry(data_file=f"data/test_trust_agents_{suffix}.json")
    for department in departments:
        agent_id = f"{department}_head"
        state = registry.register(_profile(agent_id, department))
        state.current_workload = 0
        state.learned_preferences = {department: -0.9} if department in disliked else {}
        state.metrics = PerformanceMetrics()
        state.relationships = {}

    budgets = BudgetManager(data_file=f"data/test_trust_budgets_{suffix}.json")
    for department in departments:
        budgets.allocate(department, budget_amount)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_trust_metrics_{suffix}.json"),
    )
    return executor, registry, budgets


def test_successful_delegation_raises_trust_in_the_delegate():
    print_section("1. A Successful Delegation Raises Trust In The Delegate")

    executor, registry, _ = _make_executor(
        ["engineering", "design"], disliked=["engineering"], suffix="success")
    delegator = registry.get("engineering_head")

    assert delegator.relationships == {}, "must start with no relationship history"

    result = executor.get_agent_for_department("engineering").run("Build a thing", 2.0)
    assert result["status"] == "completed"
    assert result["agent_id"] == "design_head"

    rel = delegator.relationships.get("design_head")
    print(f"  relationship after one successful delegation: {rel}")
    assert rel is not None, "update_trust_with_agent() must have run"
    assert rel.trust_score > 0.5, "a successful delegation must raise trust above the default"
    assert rel.collaboration_count == 1
    trust_after_first = rel.trust_score  # a plain float, not a reference into the mutated object

    # And a second successful delegation compounds it further, rather than
    # resetting to the default each time.
    executor.get_agent_for_department("engineering").run("Build another thing", 2.0)
    rel2 = delegator.relationships["design_head"]
    assert rel2.collaboration_count == 2
    assert rel2.trust_score > trust_after_first, "trust must keep climbing on repeated success"


def test_failed_delegation_lowers_trust():
    print_section("2. A Delegation The Delegate Could Not Afford Lowers Trust")

    # design gets no budget at all, so the delegate's own request_expense()
    # denies the task and it comes back escalated - a real failure outcome,
    # not a contrived one.
    executor, registry, budgets = _make_executor(
        ["engineering", "design"], disliked=["engineering"], budget_amount=0, suffix="fail")
    budgets.allocate("engineering", 100000)  # engineering needs to afford deciding, not executing
    delegator = registry.get("engineering_head")

    result = executor.get_agent_for_department("engineering").run("Build a thing", 2.0)
    print(f"  delegate outcome: status={result.get('status')} agent_id={result.get('agent_id')}")
    assert result["status"] == "escalated", "design has no budget - the delegate must escalate"

    rel = delegator.relationships.get("design_head")
    print(f"  relationship after one failed delegation: {rel}")
    assert rel is not None
    assert rel.trust_score < 0.5, "a failed delegation must lower trust below the default"
    assert rel.collaboration_count == 1


def test_trust_then_actually_breaks_ties_in_delegate_ranking():
    """Closes the loop: not just that trust moves, but that a delegate who
    has earned more trust is preferred over one who hasn't, all else equal -
    confirming rank_candidate()'s existing 5% trust term (which already read
    real relationship data correctly) finally has real data to read."""
    print_section("3. Earned Trust Actually Changes Who Gets Picked")

    from agent_decisions import AgentDecisionEngine, DecisionContext

    registry = AgentRegistry(data_file="data/test_trust_ranking.json")
    delegator = registry.register(_profile("delegator_head", "eng"))
    delegator.current_workload = 0
    delegator.relationships = {}
    trusted = registry.register(_profile("trusted_head", "design"))
    trusted.current_workload = 0
    trusted.metrics = PerformanceMetrics()
    untrusted = registry.register(_profile("untrusted_head", "design"))
    untrusted.current_workload = 0
    untrusted.metrics = PerformanceMetrics()

    # Same mechanism the fix uses, driven directly rather than through ten
    # delegation round-trips - this test is about the ranking consequence,
    # test 1 above already covers the accumulation itself.
    for _ in range(5):
        delegator.update_trust_with_agent("trusted_head", 0.1)
        delegator.update_trust_with_agent("untrusted_head", -0.1)

    engine = AgentDecisionEngine(delegator, registry=registry)
    context = DecisionContext(task_id="t", task_type="design", required_skills=["shared"],
                               complexity=0.5, urgency=0.5, estimated_hours=1.0,
                               required_approval_level=2)
    best = engine.find_best_delegate(context)
    print(f"  chosen delegate: {best.profile.agent_id if best else None}")
    assert best is not None and best.profile.agent_id == "trusted_head"


if __name__ == "__main__":
    test_successful_delegation_raises_trust_in_the_delegate()
    test_failed_delegation_lowers_trust()
    test_trust_then_actually_breaks_ties_in_delegate_ranking()
    print("\n✅ All delegation-trust-feedback tests passed!\n")
