#!/usr/bin/env python3
"""
Test that find_best_delegate() ranking rewards covering MORE of a task's
required_skills, not just any overlap.

Checkpoint 40 fixed the *filter* (checked overlap with the full
required_skills set instead of just required_skills[0]) but left its own
follow-up open: "Ranking does not reward covering more of the required
skills... a candidate matching one required skill and a candidate matching
all of them score identically on the skill axis (skill_level / 5.0)."

Confirmed live before this fix, with two skill_level-4 candidates and
required_skills=["deployment", "architecture"]:
  partial_match (expertise=["deployment"])                -> rank 0.72
  full_match    (expertise=["deployment", "architecture"]) -> rank 0.72
Identical scores, so max() picked whichever came first in registry
iteration order - here, the partial match - even though the full match is
a strictly better delegate. find_best_delegate() returned "partial_match".

rank_candidate() now scores a `coverage` factor - the fraction of
required_skills a candidate's expertise_areas actually satisfies - so a
candidate covering more of what the task needs outranks one covering less,
all else equal.
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


def _profile(agent_id: str, department: str, expertise, skill_level: int = 4) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=list(expertise), skill_level=skill_level,
        capabilities=[], constraints=[], max_concurrent_tasks=4,
    )


def _registry(suffix: str) -> AgentRegistry:
    return AgentRegistry(data_file=f"data/test_coverage_ranking_{suffix}.json")


def _reset(state) -> None:
    # register() returns an EXISTING state if a prior run left one on disk,
    # and workload/metrics accumulate across runs - reset what this test
    # asserts on rather than assuming a clean file.
    state.current_workload = 0
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()


def test_full_coverage_beats_partial_coverage_at_equal_skill_level():
    """The core regression: same skill_level, same everything else, but one
    candidate covers both required skills and the other covers only one -
    the fuller match must win, not whichever the pool happens to list first."""
    print_section("1. Full Required-Skill Coverage Outranks Partial Coverage")

    registry = _registry("full_vs_partial")
    delegator = registry.register(_profile("ops_head", "ops", ["ops"]))
    # Registered BEFORE full_match, so a tie would resolve to this one via
    # max()'s first-occurrence behavior - proving any win for full_match
    # is the ranking formula's doing, not iteration order.
    partial = registry.register(_profile("partial_match", "eng", ["deployment"]))
    full = registry.register(_profile("full_match", "eng2", ["deployment", "architecture"]))
    for state in (delegator, partial, full):
        _reset(state)

    context = DecisionContext(
        task_id="t1", task_type="ops",
        required_skills=["deployment", "architecture"],
        complexity=0.5, urgency=0.5, estimated_hours=2.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills          : {context.required_skills}")
    print(f"  partial_match expertise  : {partial.profile.expertise_areas}")
    print(f"  full_match expertise     : {full.profile.expertise_areas}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")

    assert delegate is not None
    assert delegate.profile.agent_id == "full_match", (
        "full_match covers every required skill and must outrank a candidate "
        "covering only one, even at equal skill_level"
    )


def test_higher_skill_level_can_still_outweigh_lower_coverage():
    """Coverage is one factor among several, not an override - a much more
    skilled candidate with partial coverage can still beat a bare-minimum
    candidate with full coverage. Guards against a fix that makes coverage
    the only thing that matters."""
    print_section("2. Skill Level Still Counts, Not Just Coverage")

    registry = _registry("skill_vs_coverage")
    delegator = registry.register(_profile("ops_head", "ops", ["ops"]))
    expert_partial = registry.register(
        _profile("expert_partial", "eng", ["deployment"], skill_level=5)
    )
    novice_full = registry.register(
        _profile("novice_full", "eng2", ["deployment", "architecture"], skill_level=1)
    )
    for state in (delegator, expert_partial, novice_full):
        _reset(state)

    context = DecisionContext(
        task_id="t2", task_type="ops",
        required_skills=["deployment", "architecture"],
        complexity=0.5, urgency=0.5, estimated_hours=2.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  expert_partial: skill=5, expertise={expert_partial.profile.expertise_areas}")
    print(f"  novice_full   : skill=1, expertise={novice_full.profile.expertise_areas}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")

    assert delegate is not None
    assert delegate.profile.agent_id == "expert_partial", (
        "coverage is a factor in ranking, not the only one - a top-skill "
        "partial match should still beat a bottom-skill full match"
    )


def test_empty_required_skills_gives_everyone_full_coverage():
    """Unchanged behavior: with no required skills, coverage defaults to 1.0
    for every candidate rather than penalizing anyone - ranking still comes
    down to skill/affinity/workload/trust exactly as before this fix."""
    print_section("3. No Required Skills Means Full Coverage For Everyone")

    registry = _registry("empty_required")
    delegator = registry.register(_profile("ops_head", "ops", ["ops"]))
    stronger = registry.register(_profile("stronger", "eng", ["architecture"], skill_level=5))
    weaker = registry.register(_profile("weaker", "eng2", ["architecture"], skill_level=2))
    for state in (delegator, stronger, weaker):
        _reset(state)

    context = DecisionContext(
        task_id="t3", task_type="ops", required_skills=[],
        complexity=0.5, urgency=0.5, estimated_hours=2.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills: [] -> find_best_delegate() = "
          f"{delegate.profile.agent_id if delegate else None}")

    assert delegate is not None and delegate.profile.agent_id == "stronger"


def main():
    print("\n" + "=" * 60)
    print("  DELEGATE COVERAGE RANKING DEMONSTRATION")
    print("=" * 60)

    for _ in range(2):  # idempotent: each test resets workload/metrics itself
        test_full_coverage_beats_partial_coverage_at_equal_skill_level()
        test_higher_skill_level_can_still_outweigh_lower_coverage()
        test_empty_required_skills_gives_everyone_full_coverage()

    print("\n" + "=" * 60)
    print("  [OK] All delegate coverage ranking tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for suffix in ["full_vs_partial", "skill_vs_coverage", "empty_required"]:
        pathlib.Path(f"data/test_coverage_ranking_{suffix}.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
