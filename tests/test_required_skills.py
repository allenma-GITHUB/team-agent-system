#!/usr/bin/env python3
"""
Test that a task's required_skills come from the task text, not from the
deciding agent's own expertise_areas - and that turning this on doesn't
manufacture false skill gaps for departments with no real declared expertise.

Before this, task_executor_v2.decide_on_task() built the DecisionContext with
`required_skills=self.agent_state.profile.expertise_areas` - the agent's OWN
skill list, standing in as "what this task requires". can_execute()'s gap
check (`set(required_skills) - set(expertise_areas)`) was therefore
mathematically always empty: an agent was always asked "do you have the
skills for this?" using its own skill list as the question. Confirmed live
before any fix: support_head (expertise customer_service/problem_solving/
documentation, skill_level 3, so the gap check is actually live for it -
see can_execute()'s `skill_level < 4` guard) executed "Redesign the platform
architecture and negotiate a new sales contract pricing strategy" cleanly.

Two things had to be true simultaneously for the fix to be safe to ship:
1. Naive substring keyword matching is wrong - "ui" (for ui_design) matched
   inside "build" ("bUId a thing"), found live via test_delegation.py, whose
   "Build a thing" silently stopped delegating because a phantom ui_design
   requirement got threaded into find_best_delegate()'s candidate filter.
2. Inferring *something* unconditionally is too broad - a department with no
   config.json "agents" entry falls back to expertise_areas=[department], a
   placeholder. Matching real vocabulary ("UI glitch") against a placeholder
   manufactures a gap that says nothing about the agent's real ability -
   found live via test_analytics_wiring.py / test_resource_gating.py, both
   of which escalated a bland "Investigate a minor UI glitch" test task
   against a throwaway qa_* department.

Each test below gets its own single-department registry/budget/analytics
files (suffix-scoped, like test_decision_inputs.py's `_agent`), not shared
across test functions - a shared registry would let a later test's
registered department head (e.g. engineering_head, which genuinely has
"architecture" in its expertise) turn into a legitimate delegate for an
earlier test's "must escalate" case once the file's idempotency loop runs a
second pass and both departments already exist on disk.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from departments import DepartmentManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent


def _agent(department: str, suffix: str) -> DepartmentHeadAgent:
    registry = AgentRegistry(data_file=f"data/test_reqskills_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_reqskills_budgets_{suffix}.json")
    agent = DepartmentHeadAgent(
        department, llm_provider=LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_reqskills_metrics_{suffix}.json"),
    )
    budgets.allocate(department, 100000)
    # register() returns the existing state if a previous run left one on
    # disk, and workload/metrics accumulate across runs.
    agent.agent_state.current_workload = 0
    agent.agent_state.metrics = PerformanceMetrics()
    return agent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_word_boundary_matching_ignores_substrings():
    """"Build a thing" must not infer ui_design from "bUId"."""
    print_section("1. Skill Keywords Match Whole Words, Not Substrings")

    skills = DepartmentManager.infer_required_skills("Build a thing")
    print(f"  inferred skills for 'Build a thing': {skills}")
    assert skills == []

    skills2 = DepartmentManager.infer_required_skills("Fix the UI glitch")
    print(f"  inferred skills for 'Fix the UI glitch': {skills2}")
    assert skills2 == ["ui_design"]


def test_no_recognized_vocabulary_infers_nothing():
    """Permissive default: absence of a keyword must not manufacture a skill."""
    print_section("2. No Match Means No Requirement, Not a Guess")

    skills = DepartmentManager.infer_required_skills("Look into a routine matter")
    print(f"  inferred skills: {skills}")
    assert skills == []


def test_mismatched_skill_escalates_for_a_configured_low_skill_head():
    """support_head (skill_level 3, real declared expertise) must now see a
    real gap on work outside customer_service/problem_solving/documentation -
    the gap check is only live below skill_level 4, so support_head is the
    one shipped agent that actually demonstrates it firing. Alone in its own
    registry, so there is no other department head around to (correctly)
    accept a delegated hand-off instead."""
    print_section("3. A Genuine Mismatch Now Escalates (support_head)")

    agent = _agent("support", "mismatch")
    decision = agent.decide_on_task(
        "Redesign the platform architecture and negotiate a new sales contract pricing strategy"
    )
    print(f"  required_skills used: {decision['context'].required_skills}")
    print(f"  decision: {decision['decision']} | {decision['raw_decision'].reasoning}")
    assert decision["context"].required_skills, "task text should infer real skill tags"
    assert decision["decision"] == "escalate"
    assert "Missing expertise" in decision["raw_decision"].reasoning


def test_matching_skill_still_executes_cleanly():
    """A task that genuinely matches the head's own expertise must not
    regress into a false escalation."""
    print_section("4. A Genuine Match Still Executes")

    agent = _agent("support", "match")
    decision = agent.decide_on_task("Troubleshoot a customer service complaint and update the documentation")
    print(f"  required_skills used: {decision['context'].required_skills}")
    print(f"  decision: {decision['decision']}")
    assert decision["decision"] == "execute"


def test_placeholder_department_is_not_gated_on_manufactured_skills():
    """A department with no config.json 'agents' entry falls back to
    expertise_areas=[department] - a placeholder. Real vocabulary in the
    task text must not be checked against it: that would only prove the
    placeholder is incomplete, not that the agent lacks a real capability."""
    print_section("5. A Placeholder Department Is Never Skill-Gated")

    agent = _agent("qa_reqskills_placeholder", "placeholder")
    assert DepartmentManager.get_agent_config(agent.agent_id) is None, \
        "this test only proves what it claims if the department really has no config entry"

    decision = agent.decide_on_task("Fix the UI glitch and redesign the architecture")
    print(f"  required_skills used: {decision['context'].required_skills}")
    print(f"  decision: {decision['decision']}")
    assert decision["context"].required_skills == []
    assert decision["decision"] == "execute"


def test_high_skill_head_is_unaffected_by_a_mismatch():
    """Documents an existing, unchanged scope limit rather than a new one:
    can_execute()'s skill-gap check only blocks agents with skill_level < 4,
    so a skill-4 head (engineering_head) still executes mismatched work as
    long as no other constraint (like cannot_execute_alone) applies. This
    checkpoint makes required_skills real; it does not touch that guard."""
    print_section("6. A Skill-4 Head Is Not Gated By The Skill Check (Unchanged)")

    agent = _agent("engineering", "highskill")
    # Low complexity on purpose, so "cannot_execute_alone" (complexity > 0.7)
    # can't be what lets this through - isolating the skill-gap guard alone.
    decision = agent.decide_on_task("Negotiate a small pricing strategy tweak with a client")
    print(f"  required_skills used: {decision['context'].required_skills}")
    print(f"  skill_level: {agent.agent_state.profile.skill_level}")
    print(f"  decision: {decision['decision']}")
    assert decision["context"].required_skills, "task text should still infer a mismatch"
    assert decision["decision"] == "execute"


def main():
    print("\n" + "=" * 60)
    print("  REQUIRED-SKILLS INFERENCE DEMONSTRATION")
    print("=" * 60)

    for _ in range(2):  # idempotent: each _agent() call resets workload/metrics
        test_word_boundary_matching_ignores_substrings()
        test_no_recognized_vocabulary_infers_nothing()
        test_mismatched_skill_escalates_for_a_configured_low_skill_head()
        test_matching_skill_still_executes_cleanly()
        test_placeholder_department_is_not_gated_on_manufactured_skills()
        test_high_skill_head_is_unaffected_by_a_mismatch()

    print("\n" + "=" * 60)
    print("  [OK] All required-skills tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for suffix in ["mismatch", "match", "placeholder", "highskill"]:
        for kind in ["agents", "budgets", "metrics"]:
            pathlib.Path(f"data/test_reqskills_{kind}_{suffix}.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
