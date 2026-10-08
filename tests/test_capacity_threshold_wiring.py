#!/usr/bin/env python3
"""
CapacityManager.is_over_capacity() had a real, correct threshold check
(utilization_pct() >= threshold) and zero callers anywhere in the repo -
not main_v2.py, not web_server.py, not task_executor_v2.py, not one of the
pre-existing test files. recommend_actions() needed the exact same
comparison and re-derived it by hand instead of calling the method that
already existed for it, and the capacity_check trace emitted in
task_executor_v2.py's own comment said "a stretched-thin department
should show up in traces" while only ever emitting the raw utilization
number, never the threshold verdict.

This pins two things:
1. recommend_actions() now actually calls is_over_capacity() - proven by
   showing the two can no longer disagree, at the exact boundary where a
   hand-rolled `>=` could silently diverge from it.
2. The capacity_check trace event now carries an "over_capacity" field
   that agrees with is_over_capacity() in both directions (over and under).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent
from agent_state import AgentRegistry, AgentProfile
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics

agent_registry = AgentRegistry(data_file="data/test_capthresh_agents.json")
budget_manager = BudgetManager(data_file="data/test_capthresh_budgets.json")
capacity_manager = CapacityManager(registry=agent_registry)
analytics = PerformanceAnalytics(data_file="data/test_capthresh_metrics.json")


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_recommend_actions_agrees_with_is_over_capacity_at_the_boundary():
    """recommend_actions()'s hiring_needed gate must be driven by
    is_over_capacity(), not a second, independent copy of the same `>=`."""
    print_section("1. recommend_actions() Uses is_over_capacity(), Not Its Own Copy")

    registry = AgentRegistry(data_file="data/test_capthresh_agents.json")
    registry.register(AgentProfile(
        agent_id="capthresh_at_edge", name="Edge Agent", agent_type="ManagerAgent",
        department="capthresh_edge", expertise_areas=["ops"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=4
    ))
    # Exactly 3/4 = 0.75 - right at a chosen threshold, so a hand-rolled
    # comparison with an off-by-direction bug (> instead of >=) would show
    # up here but agree with is_over_capacity() if both call the same code.
    registry.get("capthresh_at_edge").current_workload = 3

    cap = CapacityManager(registry=registry)
    at_boundary = cap.is_over_capacity("capthresh_edge", threshold=0.75)
    print(f"  is_over_capacity(threshold=0.75): {at_boundary}")
    assert at_boundary is True, "0.75 utilization should count as over an 0.75 threshold (>=)"

    recs = cap.recommend_actions(over_threshold=0.75, under_threshold=0.0)
    flagged = {r["department"] for r in recs if r["type"] == "hiring_needed"}
    print(f"  recommend_actions(over_threshold=0.75) flagged: {flagged}")
    assert "capthresh_edge" in flagged, (
        "recommend_actions() must flag the same department is_over_capacity() "
        "flags at the same threshold - they must be the same check."
    )

    # Just under the boundary: both must agree it's NOT over capacity.
    registry.get("capthresh_at_edge").current_workload = 2  # 2/4 = 0.5
    under = cap.is_over_capacity("capthresh_edge", threshold=0.75)
    recs_under = cap.recommend_actions(over_threshold=0.75, under_threshold=0.0)
    flagged_under = {r["department"] for r in recs_under if r["type"] == "hiring_needed"}
    print(f"  Below threshold - is_over_capacity: {under}, flagged: {flagged_under}")
    assert under is False
    assert "capthresh_edge" not in flagged_under


def _agent(department: str, **kwargs) -> DepartmentHeadAgent:
    return DepartmentHeadAgent(
        department, llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics, **kwargs
    )


def test_capacity_check_trace_reports_over_capacity_truthfully():
    """The capacity_check event's own 'over_capacity' field must match
    CapacityManager.is_over_capacity() for the department it just ran in,
    whether that department is stretched thin or has plenty of slack."""
    print_section("2. capacity_check Trace Carries a Real over_capacity Verdict")

    budget_manager.allocate("capthresh_busy", 10000)
    budget_manager.allocate("capthresh_roomy", 10000)

    # Run (and check) the busy department before the roomy one even exists
    # in the registry: once both are registered, the busy agent's own
    # decision engine would rather delegate to the idle one than execute
    # (a separate, correct behavior - see agent_decisions.py's
    # should_execute()/find_best_delegate()), and that delegation path has
    # its own trace shape. This test is about what the capacity_check event
    # itself says, not about delegation, so it keeps the two departments
    # from ever being candidates for each other.
    busy_agent = _agent("capthresh_busy")
    busy_state = agent_registry.get(busy_agent.agent_id)
    busy_state.profile.max_concurrent_tasks = 10
    busy_state.current_workload = 9  # 90%, over the 0.85 default threshold

    bus = EventBus()
    busy_agent.bus = bus
    busy_agent.run("A task to trigger a capacity_check trace")
    events = bus.get_traces("capacity_check")
    assert len(events) == 1
    print(f"  capthresh_busy: utilization={events[0].data['utilization']:.0%}, "
          f"over_capacity={events[0].data.get('over_capacity')} (expected True)")
    assert events[0].data.get("over_capacity") is True
    assert events[0].data.get("over_capacity") == capacity_manager.is_over_capacity("capthresh_busy")

    roomy_agent = _agent("capthresh_roomy")
    roomy_state = agent_registry.get(roomy_agent.agent_id)
    roomy_state.profile.max_concurrent_tasks = 10
    roomy_state.current_workload = 0  # 0% booked

    bus = EventBus()
    roomy_agent.bus = bus
    roomy_agent.run("A task to trigger a capacity_check trace")
    events = bus.get_traces("capacity_check")
    assert len(events) == 1
    print(f"  capthresh_roomy: utilization={events[0].data['utilization']:.0%}, "
          f"over_capacity={events[0].data.get('over_capacity')} (expected False)")
    assert events[0].data.get("over_capacity") is False
    assert events[0].data.get("over_capacity") == capacity_manager.is_over_capacity("capthresh_roomy")


def main():
    print("\n" + "=" * 60)
    print("  is_over_capacity() GETS ITS FIRST REAL CALLERS")
    print("=" * 60)

    test_recommend_actions_agrees_with_is_over_capacity_at_the_boundary()
    test_capacity_check_trace_reports_over_capacity_truthfully()

    print("\n" + "=" * 60)
    print("  [OK] All capacity-threshold wiring tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_capthresh_agents.json", "data/test_capthresh_budgets.json",
              "data/test_capthresh_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
