#!/usr/bin/env python3
"""
rank_candidate()'s affinity term, and decide()'s affinity_diff, both read a
candidate/delegate's learned_preferences under the wrong key, making the
affinity factor dead weight in every real delegation.

`DecisionContext.task_type` is always the *delegator's own* department -
every DecisionContext built in production (task_executor_v2.py) sets
`task_type=self.department`. `learned_preferences` is only ever written
keyed by an agent's own department too (`learn_preference(self.department,
...)` in `_run_locked()`). A delegation candidate always sits in a
different department than the delegator (one head per department in this
system), so `candidate.learned_preferences.get(context.task_type, 0)`
could never find a real entry - it misses every time and silently falls
back to the hardcoded default, making "3. Affinity for task type" (25% of
the ranking formula) a constant 0.5 for every candidate, always.

Confirmed live before this fix, with two otherwise-identical candidates
and nothing else about them different:
    support_pref=+0.9 sales_pref=-0.8 -> winner=support_head
    support_pref=-0.8 sales_pref=+0.9 -> winner=support_head  (unchanged!)
Swapping which candidate held the strong self-affinity didn't move the
winner at all - the affinity term contributed nothing to the ranking. The
same key mismatch corrupts decide()'s "delegating to someone who likes it
more" reasoning: `affinity_diff` always evaluated to
`0 - (delegator's own self-affinity)`, a number that happens to look
plausible but says nothing about the delegate actually chosen.

Fixed by reading each candidate/delegate's own department
(`candidate.profile.department`) instead of `context.task_type` - the same
question self.agent's own affinity already correctly answers elsewhere in
this file, where context.task_type happens to equal self.department for
whoever currently holds the task.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pathlib

from agent_decisions import AgentDecisionEngine, DecisionContext
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _profile(agent_id: str, department: str, expertise, skill_level: int = 4,
             max_concurrent_tasks: int = 4) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=list(expertise), skill_level=skill_level,
        capabilities=[], constraints=[], max_concurrent_tasks=max_concurrent_tasks,
    )


def _registry(suffix: str) -> AgentRegistry:
    return AgentRegistry(data_file=f"data/test_affinity_own_dept_{suffix}.json")


def _reset(state, learned_preferences=None) -> None:
    # register() returns an EXISTING state if a prior run left one on disk,
    # and workload/metrics/preferences accumulate across runs - reset what
    # this test asserts on rather than assuming a clean file.
    state.current_workload = 0
    state.learned_preferences = dict(learned_preferences or {})
    state.metrics = PerformanceMetrics()


def test_winner_flips_with_the_real_candidate_whose_own_affinity_is_higher():
    """The exact live false-negative proven above: two candidates, same
    skill/coverage/workload/trust, differing only in their own recorded
    self-affinity. Registering the weaker-affinity candidate FIRST means a
    tie (the pre-fix, always-0.5 behavior) would resolve to it via max()'s
    first-occurrence tie-break - so the real candidate winning instead
    proves affinity decided it, not registration order."""
    print_section("1. The Candidate With Higher Real Self-Affinity Wins")

    registry = _registry("winner_flip")
    delegator = registry.register(_profile("eng_head", "engineering", ["shared"]))
    weaker = registry.register(_profile("weaker_head", "weak_dept", ["shared"]))
    stronger = registry.register(_profile("stronger_head", "strong_dept", ["shared"]))
    _reset(delegator)
    _reset(weaker, {"weak_dept": -0.8})
    _reset(stronger, {"strong_dept": 0.9})

    context = DecisionContext(
        task_id="t1", task_type="engineering", required_skills=["shared"],
        complexity=0.3, urgency=0.3, estimated_hours=2.0, required_approval_level=1,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  weaker (own affinity -0.8) vs stronger (own affinity +0.9) -> "
          f"find_best_delegate() = {delegate.profile.agent_id if delegate else None}")
    assert delegate is not None and delegate.profile.agent_id == "stronger_head"

    # Swap which candidate holds the strong self-affinity - the winner must
    # follow the value, not stay pinned to whichever ranked first before.
    weaker.learned_preferences = {"weak_dept": 0.9}
    stronger.learned_preferences = {"strong_dept": -0.8}
    delegate2 = engine.find_best_delegate(context)
    print(f"  weaker (own affinity +0.9) vs stronger (own affinity -0.8) -> "
          f"find_best_delegate() = {delegate2.profile.agent_id if delegate2 else None}")
    assert delegate2 is not None and delegate2.profile.agent_id == "weaker_head"


def test_affinity_keyed_on_delegators_department_is_never_used():
    """Guards against the exact regression: a candidate whose *own*
    learned_preferences has no real entry, but who (by coincidence) has an
    entry under the delegator's department, must NOT be scored as if that
    were self-affinity - the lookup is always candidate.profile.department,
    never context.task_type."""
    print_section("2. The Delegator's Own Department Never Leaks Into A Candidate's Score")

    registry = _registry("wrong_key_unused")
    delegator = registry.register(_profile("eng_head", "engineering", ["shared"]))
    decoy = registry.register(_profile("decoy_head", "decoy_dept", ["shared"]))
    real = registry.register(_profile("real_head", "real_dept", ["shared"]))
    _reset(delegator)
    # decoy has a strong POSITIVE entry under "engineering" (the delegator's
    # department, i.e. the old buggy key) but nothing under its own
    # department - the old code would have picked this up; the fix must not.
    _reset(decoy, {"engineering": 0.9})
    # real has a strong positive entry under its OWN department - the fix
    # must find this one instead.
    _reset(real, {"real_dept": 0.9})

    context = DecisionContext(
        task_id="t2", task_type="engineering", required_skills=["shared"],
        complexity=0.3, urgency=0.3, estimated_hours=2.0, required_approval_level=1,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  decoy (fake affinity under delegator's dept) vs real (real self-affinity) -> "
          f"find_best_delegate() = {delegate.profile.agent_id if delegate else None}")
    assert delegate is not None and delegate.profile.agent_id == "real_head"


def test_decide_affinity_diff_reflects_the_delegates_real_affinity():
    """decide()'s should_execute()=False delegation branch reports an
    affinity_diff in its reasoning string. Before this fix it always
    evaluated to `0 - self.agent's own affinity`, regardless of the
    delegate actually chosen. Pinning the exact expected value against a
    delegate with a real, nonzero self-affinity (+0.7) proves the delegate
    side is now really read, not silently zero."""
    print_section("3. decide()'s affinity_diff Reads The Real Delegate, Not A Constant")

    registry = _registry("decide_diff")
    delegator = registry.register(_profile("ops_head", "ops", ["shared"]))
    delegate_candidate = registry.register(_profile("widgets_head", "widgets", ["shared"]))
    _reset(delegator, {"ops": -0.9})  # strongly dislikes its own work -> should_execute() = False
    _reset(delegate_candidate, {"widgets": 0.7})  # real, nonzero self-affinity

    context = DecisionContext(
        task_id="t3", task_type="ops", required_skills=["shared"],
        complexity=0.3, urgency=0.3, estimated_hours=2.0, required_approval_level=1,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    result = engine.decide(context)
    print(f"  decision={result.decision} assigned={result.assigned_agent_id}")
    print(f"  reasoning: {result.reasoning}")

    assert result.decision == "delegate"
    assert result.assigned_agent_id == "widgets_head"

    correct_diff = 0.7 - (-0.9)  # delegate's real self-affinity minus delegator's
    wrong_diff = 0.0 - (-0.9)    # the old bug: always 0 (missed key) minus delegator's
    assert f"({correct_diff:.1f} difference)" in result.reasoning
    assert f"({wrong_diff:.1f} difference)" not in result.reasoning, (
        "affinity_diff regressed to reading a missing key as 0 instead of the delegate's real affinity"
    )


def main():
    print("\n" + "=" * 60)
    print("  DELEGATE AFFINITY OWN-DEPARTMENT KEY TESTS")
    print("=" * 60)

    for _ in range(2):  # idempotent: each test resets workload/metrics/preferences itself
        test_winner_flips_with_the_real_candidate_whose_own_affinity_is_higher()
        test_affinity_keyed_on_delegators_department_is_never_used()
        test_decide_affinity_diff_reflects_the_delegates_real_affinity()

    print("\n" + "=" * 60)
    print("  [OK] All delegate affinity own-department tests passed!")
    print("=" * 60 + "\n")

    for suffix in ["winner_flip", "wrong_key_unused", "decide_diff"]:
        pathlib.Path(f"data/test_affinity_own_dept_{suffix}.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
