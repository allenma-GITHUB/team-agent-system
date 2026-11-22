#!/usr/bin/env python3
"""
AgentRegistry.available_agents()'s own docstring says it sorts candidates
"by affinity for task type (if known) and then by availability" - but the
sort key read learned_preferences.get(skill_required, 0). learned_preferences
is only ever written keyed by an agent's own department
(learn_preference(self.department, ...) in task_executor_v2.py's
_run_locked()), never by a skill tag. skill_required is a skill/expertise
string like "backend", and the one real production caller
(agent_decisions.py's find_best_delegate()) passes no skill_required at all -
so the lookup key was always either a string that can never be a
learned_preferences key, or None, which can't be a dict key either. The
affinity term silently evaluated to the hardcoded default for every
candidate, every time, degenerating "sort by affinity, then availability"
into "sort by availability" alone.

Confirmed live before this fix: a candidate with
learned_preferences={"engineering": 1.0} (maximal real affinity for their own
department's work) sorted behind a zero-affinity peer purely because the
peer had a lower workload - the real, present affinity value never factored
in at all, with either a skill-tag or no skill_required supplied.

Same key-mismatch shape checkpoint 99 already fixed in agent_decisions.py's
rank_candidate()/decide() (candidate.learned_preferences.get(candidate.
profile.department, ...) instead of context.task_type). Fixed here the same
way: the sort key now reads s.profile.department instead of skill_required.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _registry(suffix: str) -> AgentRegistry:
    return AgentRegistry(data_file=f"data/test_available_agents_affinity_{suffix}.json")


def _reset(state, workload: int = 0, preferences=None):
    # register() returns an existing state if a previous run left one on
    # disk - reset what this test asserts on rather than trusting a clean file.
    state.metrics = PerformanceMetrics()
    state.current_workload = workload
    state.learned_preferences = dict(preferences or {})


def test_real_affinity_for_own_department_outranks_zero_affinity_peer():
    print_section("1. Real Affinity For Own Department Beats Lower Workload")

    registry = _registry("primary")
    high_affinity = registry.register(AgentProfile(
        agent_id="eng_a", name="Eng A", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=4,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    # Carries a genuinely higher workload, so a correct affinity-first sort
    # must still place this candidate ahead of the zero-affinity peer below.
    _reset(high_affinity, workload=1, preferences={"engineering": 1.0})

    zero_affinity = registry.register(AgentProfile(
        agent_id="eng_b", name="Eng B", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=4,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    _reset(zero_affinity, workload=0, preferences={})

    with_skill_tag = registry.available_agents(skill_required="backend")
    with_no_skill_tag = registry.available_agents()

    print(f"  available_agents(skill_required='backend') -> "
          f"{[s.profile.agent_id for s in with_skill_tag]}")
    print(f"  available_agents() (no skill_required, the real production call) -> "
          f"{[s.profile.agent_id for s in with_no_skill_tag]}")

    for pool, label in ((with_skill_tag, "skill_required='backend'"), (with_no_skill_tag, "no skill_required")):
        ids = [s.profile.agent_id for s in pool]
        assert ids == ["eng_a", "eng_b"], (
            f"{label}: real affinity 1.0 for eng_a's own department must outrank "
            f"eng_b's zero affinity despite eng_a's higher workload, got {ids}"
        )


def test_no_preference_data_falls_back_to_workload_only():
    print_section("2. With No Learned Preferences At All, Sort Is Workload-Only")

    registry = _registry("no_prefs")
    busier = registry.register(AgentProfile(
        agent_id="eng_c", name="Eng C", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    _reset(busier, workload=2, preferences={})

    freer = registry.register(AgentProfile(
        agent_id="eng_d", name="Eng D", agent_type="SpecialistAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=3,
    ))
    _reset(freer, workload=0, preferences={})

    pool = registry.available_agents(skill_required="backend")
    ids = [s.profile.agent_id for s in pool]
    print(f"  available_agents() -> {ids}")

    assert ids == ["eng_d", "eng_c"], (
        f"with neither candidate having any learned preference, the lower-workload "
        f"candidate must still sort first, got {ids}"
    )


if __name__ == "__main__":
    test_real_affinity_for_own_department_outranks_zero_affinity_peer()
    test_no_preference_data_falls_back_to_workload_only()
    print("\nAll tests passed!")
