#!/usr/bin/env python3
"""
Test that find_best_delegate() considers a task's FULL required_skills set,
not just required_skills[0].

Checkpoint 38/39 both named this as a known, scoped-out gap:
infer_required_skills() can legitimately return more than one tag when a
task's text spans more than one department's vocabulary - e.g.
DepartmentManager.infer_required_skills("Troubleshoot this issue and review
the deployment pipeline") returns ["problem_solving", "deployment"]. But
find_best_delegate() only ever filtered candidates on
`required_skills[0]` ("problem_solving"). Confirmed live before the fix:
a delegator with no matching skill in its own registry got NO candidate at
all for that task, even though an engineering-department agent with
"deployment" in its expertise_areas was registered and available - a real,
qualified delegate that the ranking never even considered because it isn't
first in the inferred list.

available_agents() itself stays single-skill on purpose (it has its own
callers/tests using exactly that signature); the fix widens the net inside
find_best_delegate() to "any overlap with required_skills" instead of
"equality with required_skills[0]".
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


def _profile(agent_id: str, department: str, expertise) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=list(expertise), skill_level=4,
        capabilities=[], constraints=[], max_concurrent_tasks=4,
    )


def _registry(suffix: str) -> AgentRegistry:
    registry = AgentRegistry(data_file=f"data/test_multiskill_agents_{suffix}.json")
    return registry


def _reset(state) -> None:
    # register() returns an EXISTING state if a prior run left one on disk,
    # and workload/metrics accumulate across runs - reset what this test
    # asserts on rather than assuming a clean file.
    state.current_workload = 0
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()


def test_a_later_required_skill_can_still_surface_a_real_delegate():
    """The core regression: a candidate matching required_skills[1] (not
    required_skills[0]) must still be found."""
    print_section("1. A Match On The Second Required Skill Is Not Ignored")

    registry = _registry("secondskill")
    delegator = registry.register(_profile("sales_head", "sales",
                                            ["sales_strategy", "negotiation", "customer_relations"]))
    engineering = registry.register(_profile("engineering_head", "engineering",
                                              ["architecture", "code_review", "deployment"]))
    for state in (delegator, engineering):
        _reset(state)

    context = DecisionContext(
        task_id="t1", task_type="ops",
        required_skills=["problem_solving", "deployment"],  # [0] matches nobody
        complexity=0.5, urgency=0.5, estimated_hours=2.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills   : {context.required_skills}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")

    assert delegate is not None, (
        "engineering_head genuinely matches required_skills[1] ('deployment') "
        "and must be found even though required_skills[0] matches nobody"
    )
    assert delegate.profile.agent_id == "engineering_head"


def test_no_overlap_with_any_required_skill_still_finds_nobody():
    """Widening the filter to 'any' must not turn it into 'accept anyone' -
    a candidate matching NONE of the required skills is still excluded."""
    print_section("2. Zero Overlap Still Yields No Delegate")

    registry = _registry("nomatch")
    delegator = registry.register(_profile("sales_head", "sales",
                                            ["sales_strategy", "negotiation", "customer_relations"]))
    support = registry.register(_profile("support_head", "support",
                                          ["customer_service", "problem_solving", "documentation"]))
    for state in (delegator, support):
        _reset(state)

    context = DecisionContext(
        task_id="t2", task_type="ops",
        required_skills=["architecture", "deployment"],  # support has neither
        complexity=0.5, urgency=0.5, estimated_hours=2.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills   : {context.required_skills}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")
    assert delegate is None, "support_head matches neither required skill and must not be picked"


def test_empty_required_skills_still_considers_everyone():
    """Unchanged behavior: no required skills means no skill filtering at
    all, same as before this fix."""
    print_section("3. No Required Skills Means No Skill Filter (Unchanged)")

    registry = _registry("empty")
    delegator = registry.register(_profile("sales_head", "sales", ["sales_strategy"]))
    engineering = registry.register(_profile("engineering_head", "engineering", ["architecture"]))
    for state in (delegator, engineering):
        _reset(state)

    context = DecisionContext(
        task_id="t3", task_type="ops", required_skills=[],
        complexity=0.5, urgency=0.5, estimated_hours=2.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills   : {context.required_skills}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")
    assert delegate is not None and delegate.profile.agent_id == "engineering_head"


def test_self_is_still_excluded_with_multiple_required_skills():
    """The self-exclusion guard (checkpoint documented in test_delegation.py)
    must keep working now that the candidate pool is fetched unfiltered by
    skill and filtered here instead."""
    print_section("4. An Agent Still Never Delegates To Itself")

    registry = _registry("selfexclude")
    solo = registry.register(_profile("solo_head", "solo", ["architecture", "deployment"]))
    _reset(solo)

    context = DecisionContext(
        task_id="t4", task_type="ops",
        required_skills=["architecture", "deployment"],
        complexity=0.5, urgency=0.5, estimated_hours=1.0, required_approval_level=2,
    )

    engine = AgentDecisionEngine(solo, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  only agent in registry matches both required skills but is the caller itself")
    print(f"  find_best_delegate() -> {delegate}")
    assert delegate is None, "an agent must never be its own delegate"


def main():
    print("\n" + "=" * 60)
    print("  MULTI-SKILL DELEGATION MATCHING DEMONSTRATION")
    print("=" * 60)

    for _ in range(2):  # idempotent: each test resets workload/metrics itself
        test_a_later_required_skill_can_still_surface_a_real_delegate()
        test_no_overlap_with_any_required_skill_still_finds_nobody()
        test_empty_required_skills_still_considers_everyone()
        test_self_is_still_excluded_with_multiple_required_skills()

    print("\n" + "=" * 60)
    print("  [OK] All multi-skill delegation tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for suffix in ["secondskill", "nomatch", "empty", "selfexclude"]:
        pathlib.Path(f"data/test_multiskill_agents_{suffix}.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
