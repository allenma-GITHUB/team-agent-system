#!/usr/bin/env python3
"""
AgentDecisionEngine.decide() silently executed a task it had just decided
NOT to execute, whenever no delegate was available.

should_execute()=False means the engine has already concluded this agent
either dislikes the task (affinity < -0.5) or is too busy for an urgent one
(workload >= 80% of capacity and context.urgency >= 0.5) and should hand it
off. decide()'s should_execute()-False branch tries exactly that - but if
find_best_delegate() returns None (nobody else is a legal candidate), the
branch had no `else`: execution fell straight through to the same "We're
executing" return used for a freely-chosen task, with the identical
reasoning string and the identical skill-scaled confidence formula (up to
0.95). Confirmed live before this fix: a solo agent with no one else
registered, given a task of a type it rated -0.9 (strongly disliked), got
back decision="execute", confidence=0.9, reasoning="Executing with skill
level 4/5, affinity: -0.9" - nothing anywhere in that result distinguished
it from a task the agent actually wanted.

This is the same silent-fallback shape task_executor_v2._dispatch_delegation's
own docstring names as the bug it exists to avoid ("never a silent fallback
that lets the trace keep claiming the work went somewhere it didn't"), one
layer up: here the trace doesn't claim a delegation that didn't happen, but
it does claim a confident, preferred decision that the engine itself had
just overridden for lack of any alternative.

Fixed: decide() now tracks whether should_execute() said no and no delegate
was found, and if so, names the override in `reasoning` and reports a flat
confidence=0.6 - the same value used for the parallel can_execute()=False/
no-delegate escalation above it, because the uncertainty here is about the
DECISION (this agent didn't want the task and had no choice), not about its
skill at performing it. `decision` stays "execute" and `approval_required`
is unchanged (still requires_approval()'s own call) - the task still gets
done, by the only agent that can do it; only the record of how confidently
that happened changes.
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


def _solo_engine(registry_suffix: str, max_concurrent_tasks: int = 3) -> AgentDecisionEngine:
    """A lone agent, nobody else registered - find_best_delegate() must
    always return None, so should_execute()=False has nowhere to go."""
    registry = AgentRegistry(data_file=f"data/test_forced_exec_{registry_suffix}.json")
    profile = AgentProfile(
        agent_id="solo_head", name="Solo Head", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend", "deployment"],
        skill_level=4, capabilities=[], constraints=[],
        max_concurrent_tasks=max_concurrent_tasks,
    )
    state = registry.register(profile)
    # register() returns an existing state if a previous run left one on
    # disk - reset what this test asserts on rather than trusting a clean file.
    state.metrics = PerformanceMetrics()
    state.current_workload = 0
    state.active_hours = 0.0
    state.learned_preferences = {}
    return AgentDecisionEngine(state, registry=registry), state


def _context(task_type: str, estimated_hours: float = 2.0, urgency: float = 0.2) -> DecisionContext:
    return DecisionContext(
        task_id="t1", task_type=task_type, required_skills=["backend"],
        complexity=0.3, urgency=urgency, estimated_hours=estimated_hours,
        required_approval_level=1,
    )


def test_disliked_task_with_no_delegate_discloses_the_override():
    print_section("1. Disliked Task, No Delegate: decide() Must Say So")

    engine, state = _solo_engine("disliked")
    state.learned_preferences["code_review"] = -0.9  # strongly disliked
    context = _context("code_review")

    can_do, _ = engine.can_execute(context)
    should = engine.should_execute(context)
    delegate = engine.find_best_delegate(context)
    result = engine.decide(context)

    print(f"  can_execute={can_do} should_execute={should} delegate={delegate}")
    print(f"  decide() -> decision={result.decision} confidence={result.confidence}")
    print(f"              reasoning={result.reasoning!r}")

    assert can_do is True, "setup must reach the should_execute branch, not can_execute's"
    assert should is False, "affinity -0.9 must trip should_execute's dislike branch"
    assert delegate is None, "a solo agent must never find a delegate"

    assert result.decision == "execute", "the only capable agent must still do the work"
    assert result.assigned_agent_id == "solo_head"
    assert result.confidence == 0.6, (
        "forced execution must report the same reduced, flat confidence as the "
        "can_execute()=False/no-delegate escalation, not the skill-scaled formula")
    assert "no suitable delegate" in result.reasoning.lower()
    assert "-0.9" in result.reasoning, "the disliked affinity itself must be named, not hidden"


def test_full_and_urgent_with_no_delegate_also_discloses():
    print_section("2. Full-And-Urgent, No Delegate: Same Disclosure")

    engine, state = _solo_engine("full_urgent", max_concurrent_tasks=5)
    state.current_workload = 4  # 80% of 5 - trips should_execute()'s "full" check
    context = _context("deployment", estimated_hours=1.0, urgency=0.9)  # urgent

    should = engine.should_execute(context)
    result = engine.decide(context)

    print(f"  workload=4/5 urgency=0.9 should_execute={should}")
    print(f"  decide() -> decision={result.decision} confidence={result.confidence}")
    print(f"              reasoning={result.reasoning!r}")

    assert should is False, "full (>=80%) and urgent (>=0.5) must prefer delegation"
    assert result.decision == "execute"
    assert result.confidence == 0.6, "the full-and-urgent fallback must be disclosed identically"
    assert "no suitable delegate" in result.reasoning.lower()


def test_approval_required_is_unaffected_by_the_disclosure():
    """The fix changes confidence/reasoning, not the approval policy -
    requires_approval() still decides approval_required exactly as before."""
    print_section("3. approval_required Still Comes From requires_approval()")

    engine, state = _solo_engine("approval", max_concurrent_tasks=5)
    state.learned_preferences["design"] = -0.9
    # complexity > 0.8 forces requires_approval()=True independent of this fix.
    high_complexity = DecisionContext(
        task_id="t2", task_type="design", required_skills=["backend"],
        complexity=0.9, urgency=0.1, estimated_hours=1.0, required_approval_level=1,
    )
    low_complexity = DecisionContext(
        task_id="t3", task_type="design", required_skills=["backend"],
        complexity=0.2, urgency=0.1, estimated_hours=1.0, required_approval_level=1,
    )

    forced_high = engine.decide(high_complexity)
    state.current_workload = 0  # decide() doesn't mutate it, but keep each call identical
    forced_low = engine.decide(low_complexity)

    print(f"  complexity=0.9 -> approval_required={forced_high.approval_required}")
    print(f"  complexity=0.2 -> approval_required={forced_low.approval_required}")

    assert forced_high.confidence == 0.6 and forced_low.confidence == 0.6
    assert forced_high.approval_required is True, "requires_approval()'s complexity>0.8 rule must still fire"
    assert forced_low.approval_required is False, "a low-complexity forced execution must not need approval"


def test_normal_preferred_execution_is_unaffected():
    """The overwhelmingly common path - an agent that wants its own task,
    or has a real delegate available - must be untouched by this fix."""
    print_section("4. Ordinary Execution And Real Delegation Still Work")

    engine, state = _solo_engine("normal")
    context = _context("backend")  # neutral affinity, moderate workload -> should_execute=True

    assert engine.should_execute(context) is True
    result = engine.decide(context)
    print(f"  neutral affinity -> decide() = {result.decision}, confidence={result.confidence}")
    print(f"                       reasoning={result.reasoning!r}")

    assert result.decision == "execute"
    assert result.confidence == min(0.95, 0.5 + state.profile.skill_level * 0.1), (
        "an agent that wanted the task must keep the original skill-scaled confidence")
    assert "no suitable delegate" not in result.reasoning.lower()

    # Now give the disliked agent a real delegate - the delegate branch,
    # unreached by this fix, must still win outright.
    registry = AgentRegistry(data_file="data/test_forced_exec_with_delegate.json")
    disliker = registry.register(AgentProfile(
        agent_id="disliker_head", name="Disliker", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    disliker.metrics = PerformanceMetrics()
    disliker.current_workload = 0
    disliker.learned_preferences = {"backend": -0.9}
    fan = registry.register(AgentProfile(
        agent_id="fan_head", name="Fan", agent_type="SpecialistAgent",
        department="design", expertise_areas=["backend"], skill_level=4,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    fan.metrics = PerformanceMetrics()
    fan.current_workload = 0
    fan.learned_preferences = {"backend": 0.9}

    engine2 = AgentDecisionEngine(disliker, registry=registry)
    delegated = engine2.decide(_context("backend"))
    print(f"  real delegate available -> decide() = {delegated.decision} "
          f"-> {delegated.assigned_agent_id} (confidence={delegated.confidence})")

    assert delegated.decision == "delegate"
    assert delegated.assigned_agent_id == "fan_head"
    assert delegated.confidence == 0.75, "the pre-existing delegate path's own confidence must be untouched"


if __name__ == "__main__":
    test_disliked_task_with_no_delegate_discloses_the_override()
    test_full_and_urgent_with_no_delegate_also_discloses()
    test_approval_required_is_unaffected_by_the_disclosure()
    test_normal_preferred_execution_is_unaffected()

    print("\n✅ All forced-execution-disclosure tests passed!\n")
