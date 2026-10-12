#!/usr/bin/env python3
"""
AgentState.is_available(task_hours) accepted and documented a `task_hours`
parameter ("Check if agent has capacity for new task") since Phase 1, but
never referenced it in the body - it only ever checked current_workload
against max_concurrent_tasks and whether max_hours_per_week was <= 0 (a
static on/off switch, not a cap). Confirmed live before writing any fix:

    state.current_workload = 1              # well under max_concurrent_tasks
    state.is_available(1)                    -> True
    state.is_available(1000)                 -> True
    state.is_available(10_000_000)            -> True

Identical outcome no matter how large task_hours got - the parameter named
for exactly this check could not, by construction, affect the result.
max_hours_per_week (default 40.0, and never overridden anywhere in
config.json or code) sat right there the whole time as the field this
should have been compared against.

Fix, same shape as checkpoint 62's urgency fix - a producer and a consumer,
both needed to make this observable outside a hand-built call:
1. AgentState gains `active_hours`, incremented by add_task(hours) and
   released by remove_task(hours), mirroring current_workload's own
   lifecycle exactly (a live in-flight total, not a true calendar-week
   accumulator - see agent_state.py's comment on the field for why that's
   an honest limitation, not a bug this fix claims to close).
2. is_available() now rejects a task whose hours, added to what's already
   active, would exceed max_hours_per_week.

This pins both the unit-level behavior and that it actually changes what
the real production call path (DepartmentHeadAgent.run(), not a hand-built
DecisionContext) does with a task too large for one agent's week.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib

from agent_decisions import AgentDecisionEngine, DecisionContext
from agent_state import AgentProfile, AgentRegistry, AgentState
from budgets import BudgetManager, CapacityManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _profile(**overrides) -> AgentProfile:
    defaults = dict(
        agent_id="hours_agent", name="Hours Agent", agent_type="ManagerAgent",
        department="engineering", expertise_areas=["engineering"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=5, max_hours_per_week=40.0,
    )
    defaults.update(overrides)
    return AgentProfile(**defaults)


def test_is_available_now_reads_task_hours():
    print_section("1. is_available() Actually Reads task_hours")

    state = AgentState(profile=_profile())
    state.current_workload = 1  # well under max_concurrent_tasks=5

    small = state.is_available(1)
    huge = state.is_available(10_000_000)
    print(f"  current_workload=1/5, max_hours_per_week=40: "
          f"is_available(1)={small}, is_available(10,000,000)={huge}")
    assert small is True
    assert huge is False, (
        "a 10,000,000-hour task must not be 'available' just because a "
        "concurrent-task slot is free - pre-fix this returned True "
        "identically regardless of task_hours"
    )

    # Right at the boundary: exactly max_hours_per_week fits, one hour over does not.
    at_cap = state.is_available(40.0)
    over_cap = state.is_available(40.01)
    print(f"  is_available(40.0)={at_cap}, is_available(40.01)={over_cap}")
    assert at_cap is True
    assert over_cap is False


def test_add_task_and_remove_task_track_active_hours_symmetrically():
    print_section("2. add_task()/remove_task() Keep active_hours In Lockstep")

    state = AgentState(profile=_profile(max_concurrent_tasks=10))
    assert state.active_hours == 0.0

    state.add_task(30.0)
    print(f"  after add_task(30): active_hours={state.active_hours}, "
          f"is_available(15)={state.is_available(15)}")
    assert state.active_hours == 30.0
    # 30 already committed + 15 more would be 45 > 40.
    assert state.is_available(15) is False
    # But a small top-up that still fits is fine.
    assert state.is_available(5) is True

    state.remove_task(30.0)
    print(f"  after remove_task(30): active_hours={state.active_hours}, "
          f"is_available(15)={state.is_available(15)}")
    assert state.active_hours == 0.0
    assert state.is_available(15) is True

    # remove_task() never drives active_hours negative, same floor
    # current_workload's own remove_task() has always had.
    state.remove_task(5.0)
    assert state.active_hours == 0.0


def test_can_execute_rejects_an_oversized_task():
    print_section("3. can_execute() Rejects A Task Too Big For The Week")

    state = AgentState(profile=_profile())
    engine = AgentDecisionEngine(state)

    normal = DecisionContext(
        task_id="t1", task_type="engineering", required_skills=[],
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    oversized = DecisionContext(
        task_id="t2", task_type="engineering", required_skills=[],
        complexity=0.3, urgency=0.5, estimated_hours=1000.0, required_approval_level=1,
    )

    ok, reason = engine.can_execute(normal)
    print(f"  2-hour task: can_execute={ok} ({reason})")
    assert ok is True

    ok, reason = engine.can_execute(oversized)
    print(f"  1000-hour task: can_execute={ok} ({reason})")
    assert ok is False
    assert "capacity" in reason.lower() or "workload" in reason.lower()


def test_decide_escalates_oversized_task_with_no_delegate():
    print_section("4. decide() Escalates Rather Than Silently Executing")

    registry = AgentRegistry(data_file="data/test_hourscap_agents.json")
    registry.register(_profile(agent_id="hourscap_lone_head", department="hourscap_lone"))
    state = registry.get("hourscap_lone_head")
    state.current_workload = 0
    state.active_hours = 0.0

    engine = AgentDecisionEngine(state, registry=registry)
    oversized = DecisionContext(
        task_id="t3", task_type="hourscap_lone", required_skills=[],
        complexity=0.3, urgency=0.5, estimated_hours=500.0, required_approval_level=1,
    )
    decision = engine.decide(oversized)
    print(f"  decision={decision.decision!r}, approval_required={decision.approval_required}")
    assert decision.decision == "escalate", (
        "no delegate exists for this lone agent, so a task too big for its "
        "week must escalate, never fall through and execute as if nothing "
        "was wrong"
    )
    assert decision.approval_required is True

    pathlib.Path("data/test_hourscap_agents.json").unlink(missing_ok=True)


def test_normal_sized_tasks_unaffected():
    """Regression guard: DepartmentManager.estimate_hours() never produces
    more than HIGH_EFFORT_HOURS (16.0), well under the 40-hour default cap -
    ordinary, auto-estimated tasks must see no behavior change at all."""
    print_section("5. Ordinary Tasks Stay Unaffected")

    state = AgentState(profile=_profile())
    for hours in (0.5, 1.0, 16.0):
        available = state.is_available(hours)
        print(f"  is_available({hours})={available}")
        assert available is True


def test_end_to_end_oversized_task_escalates_through_real_agent():
    """Through the real production call path - DepartmentHeadAgent.run(),
    not a hand-built DecisionContext - a task whose estimated_hours alone
    blow the agent's weekly cap comes back escalated, not completed."""
    print_section("6. End To End: DepartmentHeadAgent.run() With An Oversized Task")

    registry = AgentRegistry(data_file="data/test_hourscap_e2e_agents.json")
    budgets = BudgetManager(data_file="data/test_hourscap_e2e_budgets.json")
    budgets.allocate("hourscap_e2e", 1_000_000)
    analytics = PerformanceAnalytics(data_file="data/test_hourscap_e2e_metrics.json")

    agent = DepartmentHeadAgent(
        "hourscap_e2e", llm_provider=LLMProvider(), agent_state_registry=registry,
        budget_manager=budgets, capacity_manager=CapacityManager(registry=registry),
        analytics=analytics,
    )

    normal_result = agent.run("Review a config file", estimated_hours=2.0)
    print(f"  2-hour task -> status={normal_result['status']!r}")
    assert normal_result["status"] == "completed"

    oversized_result = agent.run("Rebuild the entire platform from scratch", estimated_hours=2000.0)
    print(f"  2000-hour task -> status={oversized_result['status']!r}, "
          f"approved={oversized_result['approved']}")
    assert oversized_result["status"] == "escalated"
    assert oversized_result["approved"] is False

    for f in ["data/test_hourscap_e2e_agents.json", "data/test_hourscap_e2e_budgets.json",
              "data/test_hourscap_e2e_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


def main():
    print("\n" + "=" * 60)
    print("  is_available() FINALLY READS task_hours")
    print("=" * 60)

    test_is_available_now_reads_task_hours()
    test_add_task_and_remove_task_track_active_hours_symmetrically()
    test_can_execute_rejects_an_oversized_task()
    test_decide_escalates_oversized_task_with_no_delegate()
    test_normal_sized_tasks_unaffected()
    test_end_to_end_oversized_task_escalates_through_real_agent()

    print("\n" + "=" * 60)
    print("  [OK] All task-hours capacity tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
