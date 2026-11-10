#!/usr/bin/env python3
"""
find_best_delegate() could hand a task to an agent its own capacity check
says cannot take it.

AgentState.is_available(task_hours) has checked task_hours against
max_hours_per_week correctly since it was fixed: a candidate already
carrying 38 of their 40 weekly hours correctly reports unavailable for a
10-hour task (is_available(10.0) -> False) while still reporting available
for a 1-hour one (is_available(1.0) -> True). But AgentRegistry.available_
agents() - the only thing find_best_delegate() ever calls - hardcoded
`task_hours=1.0` into that same check regardless of the real task, and
find_best_delegate() called it with no arguments at all. Confirmed live
before this fix: a candidate at 38/40 weekly hours came back "available" in
available_agents(), got picked as the best delegate for a 10-hour task, and
decide() returned decision="delegate", assigned_agent_id=<that candidate>,
confidence=0.8 - identical in shape to delegating to someone with real
capacity, even though that same candidate's own is_available(10.0) says
False.

Fixed: available_agents() now takes task_hours (default 1.0, preserving
every caller that doesn't know or care about the task's real size) and
checks is_available(task_hours) instead of a hardcoded 1.0.
find_best_delegate() now passes context.estimated_hours through. decide()
itself is untouched - both of its call sites to find_best_delegate() now
simply get an honest candidate pool.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_decisions import AgentDecisionEngine, DecisionContext
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _registry(suffix: str) -> AgentRegistry:
    return AgentRegistry(data_file=f"data/test_delegate_capacity_{suffix}.json")


def _reset(state, workload: int = 0, active_hours: float = 0.0, preferences=None):
    # register() returns an existing state if a previous run left one on
    # disk - reset what this test asserts on rather than trusting a clean file.
    state.metrics = PerformanceMetrics()
    state.current_workload = workload
    state.active_hours = active_hours
    state.learned_preferences = dict(preferences or {})


def _context(estimated_hours: float, urgency: float = 0.2) -> DecisionContext:
    return DecisionContext(
        task_id="t1", task_type="backend_work", required_skills=["backend"],
        complexity=0.3, urgency=urgency, estimated_hours=estimated_hours,
        required_approval_level=1,
    )


def test_over_capacity_delegate_is_excluded_and_decide_escalates():
    print_section("1. Over-Capacity Delegate: Excluded, decide() Escalates")

    registry = _registry("over_capacity")
    caller = registry.register(AgentProfile(
        agent_id="caller_head", name="Caller", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    _reset(caller, workload=3)  # full -> can_execute()=False, must try to delegate

    delegate = registry.register(AgentProfile(
        agent_id="delegate_head", name="Delegate", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3, max_hours_per_week=40.0,
    ))
    _reset(delegate, workload=0, active_hours=38.0)  # only 2h of weekly capacity left

    context = _context(estimated_hours=10.0)
    engine = AgentDecisionEngine(caller, registry=registry)

    direct_check = delegate.is_available(10.0)
    pool = registry.available_agents(task_hours=10.0)
    best = engine.find_best_delegate(context)
    result = engine.decide(context)

    print(f"  delegate.is_available(10.0) directly = {direct_check}")
    print(f"  available_agents(task_hours=10.0) = {[s.profile.agent_id for s in pool]}")
    print(f"  find_best_delegate -> {best.profile.agent_id if best else None}")
    print(f"  decide() -> decision={result.decision} assigned={result.assigned_agent_id}")

    assert direct_check is False, "setup must put the delegate genuinely over capacity for this task"
    assert pool == [], "available_agents() must exclude a candidate the task's real hours would overflow"
    assert best is None, "find_best_delegate() must not hand a 10h task to a 2h-capacity candidate"
    assert result.decision == "escalate", "with no capable delegate, decide() must escalate, not delegate"
    assert result.assigned_agent_id is None


def test_under_capacity_delegate_is_still_found():
    print_section("2. Same Delegate, Smaller Task: Still Found")

    registry = _registry("under_capacity")
    caller = registry.register(AgentProfile(
        agent_id="caller_head2", name="Caller2", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    _reset(caller, workload=3)

    delegate = registry.register(AgentProfile(
        agent_id="delegate_head2", name="Delegate2", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3, max_hours_per_week=40.0,
    ))
    _reset(delegate, workload=0, active_hours=38.0)  # same 2h of weekly capacity left

    context = _context(estimated_hours=1.0)  # fits in the 2h remaining
    engine = AgentDecisionEngine(caller, registry=registry)
    best = engine.find_best_delegate(context)
    result = engine.decide(context)

    print(f"  find_best_delegate -> {best.profile.agent_id if best else None}")
    print(f"  decide() -> decision={result.decision} assigned={result.assigned_agent_id} "
          f"confidence={result.confidence}")

    assert best is not None and best.profile.agent_id == "delegate_head2"
    assert result.decision == "delegate"
    assert result.assigned_agent_id == "delegate_head2"
    assert result.confidence == 0.8, "the pre-existing can_execute()=False delegate path's confidence is untouched"


def test_should_execute_false_branch_also_respects_capacity():
    """Checkpoint 92's should_execute()=False branch calls find_best_delegate()
    too - this fix must not be confined to the can_execute()=False path above."""
    print_section("3. should_execute()=False Branch Also Excludes Over-Capacity Delegates")

    registry = _registry("should_execute_branch")
    disliker = registry.register(AgentProfile(
        agent_id="disliker_head", name="Disliker", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=4,
        capabilities=[], constraints=[], max_concurrent_tasks=5,
    ))
    _reset(disliker, workload=0, preferences={"backend_work": -0.9})  # strongly disliked

    overloaded_delegate = registry.register(AgentProfile(
        agent_id="overloaded_head", name="Overloaded", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3, max_hours_per_week=40.0,
    ))
    _reset(overloaded_delegate, workload=0, active_hours=38.0)

    context = _context(estimated_hours=10.0)  # disliker can_execute, but should_execute=False
    engine = AgentDecisionEngine(disliker, registry=registry)

    assert engine.can_execute(context)[0] is True
    assert engine.should_execute(context) is False

    result = engine.decide(context)
    print(f"  decide() -> decision={result.decision} assigned={result.assigned_agent_id} "
          f"confidence={result.confidence}")
    print(f"              reasoning={result.reasoning!r}")

    assert result.decision == "execute", (
        "disliked task, and the only other registered agent cannot actually take it - "
        "the disliker must still be the one who executes it")
    assert result.assigned_agent_id == "disliker_head"
    assert result.confidence == 0.6, "must disclose the forced-execution override, not delegate to an over-capacity agent"
    assert "no suitable delegate" in result.reasoning.lower()


def test_available_agents_default_is_unchanged():
    """The one other real caller (tests/test_agent_decisions.py's demo) calls
    available_agents() with no task_hours at all - the new parameter's
    default must keep its behavior exactly as before."""
    print_section("4. available_agents() With No task_hours Argument Is Unchanged")

    registry = _registry("default_arg")
    agent = registry.register(AgentProfile(
        agent_id="default_head", name="Default", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3, max_hours_per_week=40.0,
    ))
    _reset(agent, workload=0, active_hours=38.0)  # available for 1.0 more, not for 5.0 more

    default_call = registry.available_agents(skill_required="backend")
    explicit_one_hour = registry.available_agents(skill_required="backend", task_hours=1.0)
    explicit_five_hours = registry.available_agents(skill_required="backend", task_hours=5.0)

    print(f"  available_agents() default -> {[s.profile.agent_id for s in default_call]}")
    print(f"  task_hours=1.0 -> {[s.profile.agent_id for s in explicit_one_hour]}")
    print(f"  task_hours=5.0 -> {[s.profile.agent_id for s in explicit_five_hours]}")

    assert [s.profile.agent_id for s in default_call] == ["default_head"]
    assert default_call == explicit_one_hour, "the default must behave exactly like task_hours=1.0"
    assert explicit_five_hours == [], "a larger task_hours must correctly exclude a near-capacity agent"


if __name__ == "__main__":
    test_over_capacity_delegate_is_excluded_and_decide_escalates()
    test_under_capacity_delegate_is_still_found()
    test_should_execute_false_branch_also_respects_capacity()
    test_available_agents_default_is_unchanged()

    print("\n✅ All delegate-capacity-check tests passed!\n")
