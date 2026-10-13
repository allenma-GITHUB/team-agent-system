#!/usr/bin/env python3
"""
AgentState.availability_until has been declared since Phase 1, documented
as "ISO timestamp when current tasks complete," and round-tripped through
AgentRegistry.load()/save() the whole time - but confirmed live (checkpoint
64's Next Steps) to have no producer anywhere: nothing ever assigned it,
and no consumer claimed to read it either, unlike this project's more
common "wired but dead" bug shape. grep -rn "availability_until" --include
=*.py . turned up exactly the three lines declaring and round-tripping the
field, nothing that sets or reads it for a decision.

This pins the producer/consumer pair this checkpoint adds:
1. add_task()/remove_task() keep availability_until in lockstep with
   active_hours - set to "now + active_hours" whenever there's an active
   backlog, None the instant there isn't (same shape as active_hours'
   own "live projection, not a calendar-aware ETA" honesty caveat).
2. CapacityManager.earliest_relief() and recommend_actions()'s new
   earliest_relief_at field give it a real reader: an ETA for when an
   over-capacity department's own agents project clearing their current
   backlog - present only when at least one agent in that department
   actually has one, omitted otherwise (absence-means-never-computed,
   not a fabricated zero).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent
from agent_state import AgentRegistry, AgentProfile, AgentState
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _profile(agent_id: str, department: str, **kwargs) -> AgentProfile:
    defaults = dict(
        name=agent_id, agent_type="SpecialistAgent", department=department,
        expertise_areas=["ops"], skill_level=3, capabilities=[], constraints=[],
    )
    defaults.update(kwargs)
    return AgentProfile(agent_id=agent_id, **defaults)


def test_add_task_sets_availability_until_remove_task_clears_it():
    """The producer side, in isolation: a fresh agent has no backlog and no
    availability_until; add_task(hours) sets it to approximately now+hours;
    a second add_task() moves it further out by the cumulative total, not
    just the latest call's hours; remove_task() back to zero active_hours
    clears it to None again - exactly mirroring active_hours' own floor."""
    print_section("1. add_task()/remove_task() Keep availability_until In Lockstep")

    state = AgentState(profile=_profile("avail_solo", "avail_dept"))
    assert state.availability_until is None, "a fresh agent has no backlog to be available 'until'"

    before = datetime.utcnow()
    state.add_task(10.0)
    assert state.availability_until is not None
    parsed = datetime.fromisoformat(state.availability_until)
    expected = before + timedelta(hours=10.0)
    drift = abs((parsed - expected).total_seconds())
    print(f"  after add_task(10): availability_until={state.availability_until}, drift={drift:.2f}s")
    assert drift < 5, "should land within a few seconds of now+10h, not some unrelated value"

    # A second, concurrent task: active_hours is now 10+5=15, so
    # availability_until must move out to now+15, not now+5 (the new
    # call's own hours) and not stay at the first call's now+10.
    before2 = datetime.utcnow()
    state.add_task(5.0)
    parsed2 = datetime.fromisoformat(state.availability_until)
    expected2 = before2 + timedelta(hours=15.0)
    drift2 = abs((parsed2 - expected2).total_seconds())
    print(f"  after add_task(5) more (active_hours=15): availability_until={state.availability_until}, drift={drift2:.2f}s")
    assert drift2 < 5
    assert parsed2 > parsed, "committing more hours should push availability_until further out, not back"

    state.remove_task(10.0)
    assert state.availability_until is not None, "5 hours of backlog remain - still unavailable until something"
    state.remove_task(5.0)
    assert state.availability_until is None, (
        "active_hours is back to 0 - there is no backlog left to be 'available until', "
        "same floor active_hours' own remove_task() already has"
    )


def test_availability_until_persists_through_save_and_load():
    """AgentRegistry.load()/save() already round-tripped this field before
    today - confirm that still holds now that it's actually populated with
    a real value instead of always None."""
    print_section("2. availability_until Survives a save()/load() Round-Trip")

    data_file = "data/test_availability_persist.json"
    registry = AgentRegistry(data_file=data_file)
    registry.register(_profile("avail_persist", "avail_dept"))
    registry.get("avail_persist").add_task(8.0)
    stamp = registry.get("avail_persist").availability_until
    assert stamp is not None
    registry.save()

    reloaded = AgentRegistry(data_file=data_file)
    print(f"  saved: {stamp}, reloaded: {reloaded.get('avail_persist').availability_until}")
    assert reloaded.get("avail_persist").availability_until == stamp

    import pathlib
    pathlib.Path(data_file).unlink(missing_ok=True)


def test_earliest_relief_omitted_when_nobody_has_a_backlog():
    """A department can be over capacity on current_workload alone (every
    agent's task slots are full) while still having nobody with a nonzero
    active_hours recorded - e.g. every task ran before this checkpoint, or
    simply hasn't called add_task() with real hours. earliest_relief() must
    not fabricate a timestamp in that case, and recommend_actions() must
    not surface a field it can't actually back."""
    print_section("3. earliest_relief() Is Omitted, Not Guessed, With No Backlog Data")

    registry = AgentRegistry(data_file="data/test_availability_relief.json")
    registry.register(_profile("relief_agent", "relief_dept", max_concurrent_tasks=2))
    state = registry.get("relief_agent")
    state.current_workload = 2  # 100% booked by slot count alone
    assert state.availability_until is None, "workload was set directly, bypassing add_task()"

    cap = CapacityManager(registry=registry)
    assert cap.is_over_capacity("relief_dept") is True
    relief = cap.earliest_relief("relief_dept")
    print(f"  over capacity, no agent has availability_until set -> earliest_relief() = {relief!r}")
    assert relief is None

    recs = cap.recommend_actions(over_threshold=0.85, under_threshold=0.0)
    hiring = [r for r in recs if r["type"] == "hiring_needed" and r["department"] == "relief_dept"]
    assert len(hiring) == 1
    print(f"  hiring_needed recommendation: {hiring[0]}")
    assert "earliest_relief_at" not in hiring[0], (
        "no agent in the department has a real backlog projection - the field "
        "must be absent, not a fabricated or zeroed timestamp"
    )

    import pathlib
    pathlib.Path("data/test_availability_relief.json").unlink(missing_ok=True)


def test_earliest_relief_is_the_minimum_across_the_department():
    """With real backlog data, earliest_relief() must report the soonest
    any agent in the department frees up - not the latest, not an average -
    since that's the earliest point the department could take on more work
    at all. recommend_actions() must carry that exact value through."""
    print_section("4. earliest_relief() Picks the Minimum, recommend_actions() Carries It")

    registry = AgentRegistry(data_file="data/test_availability_relief_min.json")
    registry.register(_profile("relief_soon", "relief_dept2", max_concurrent_tasks=1))
    registry.register(_profile("relief_later", "relief_dept2", max_concurrent_tasks=1))
    registry.get("relief_soon").add_task(2.0)    # free soon
    registry.get("relief_later").add_task(20.0)  # free much later
    registry.get("relief_soon").current_workload = 1
    registry.get("relief_later").current_workload = 1

    cap = CapacityManager(registry=registry)
    assert cap.is_over_capacity("relief_dept2") is True

    soon_stamp = registry.get("relief_soon").availability_until
    later_stamp = registry.get("relief_later").availability_until
    relief = cap.earliest_relief("relief_dept2")
    print(f"  soon={soon_stamp}, later={later_stamp}, earliest_relief()={relief}")
    assert relief == soon_stamp, "must pick the soonest agent, not the latest or some other agent"
    assert relief != later_stamp

    recs = cap.recommend_actions(over_threshold=0.85, under_threshold=0.0)
    hiring = [r for r in recs if r["type"] == "hiring_needed" and r["department"] == "relief_dept2"]
    assert len(hiring) == 1
    assert hiring[0].get("earliest_relief_at") == soon_stamp

    import pathlib
    pathlib.Path("data/test_availability_relief_min.json").unlink(missing_ok=True)


def test_end_to_end_through_a_real_department_head_run():
    """Drive availability_until through the real production call path -
    DepartmentHeadAgent.run(), which hits task_executor_v2.py's add_task()/
    remove_task() call sites - not a hand-built AgentState. A real task
    sets availability_until while it's in flight and clears it back to
    None once execution finishes, the same lifecycle current_workload
    already has around the exact same call sites."""
    print_section("5. A Real DepartmentHeadAgent.run() Sets and Clears It")

    agent_registry = AgentRegistry(data_file="data/test_availability_e2e_agents.json")
    budget_manager = BudgetManager(data_file="data/test_availability_e2e_budgets.json")
    capacity_manager = CapacityManager(registry=agent_registry)
    analytics = PerformanceAnalytics(data_file="data/test_availability_e2e_metrics.json")
    budget_manager.allocate("avail_e2e_dept", 10000)

    agent = DepartmentHeadAgent(
        "avail_e2e_dept", llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics,
    )
    state = agent_registry.get(agent.agent_id)
    state.metrics = state.metrics.__class__()
    state.current_workload = 0
    state.active_hours = 0.0
    state.availability_until = None

    bus = EventBus()
    agent.bus = bus
    result = agent.run("Review a small config change", estimated_hours=3.0)
    print(f"  after run(): status={result.get('status')}, "
          f"active_hours={state.active_hours}, availability_until={state.availability_until}")
    assert result["status"] in ("completed", "escalated"), "must report a real terminal status"
    # _run_locked() releases the hours it reserved on every exit path
    # (success or escalation), so by the time run() returns, the backlog
    # this one task committed is gone again.
    assert state.active_hours == 0.0
    assert state.availability_until is None, (
        "once the one in-flight task this run committed has been released, "
        "there is no backlog left - availability_until must follow active_hours back to None"
    )

    import pathlib
    for f in ["data/test_availability_e2e_agents.json", "data/test_availability_e2e_budgets.json",
              "data/test_availability_e2e_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


def main():
    print("\n" + "=" * 60)
    print("  availability_until GETS A PRODUCER AND A CONSUMER")
    print("=" * 60)

    test_add_task_sets_availability_until_remove_task_clears_it()
    test_availability_until_persists_through_save_and_load()
    test_earliest_relief_omitted_when_nobody_has_a_backlog()
    test_earliest_relief_is_the_minimum_across_the_department()
    test_end_to_end_through_a_real_department_head_run()

    print("\n" + "=" * 60)
    print("  [OK] All availability_until tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
