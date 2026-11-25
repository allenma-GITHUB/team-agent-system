#!/usr/bin/env python3
"""
Checkpoint 108. departments.py's infer_required_skills() scanned
_SKILL_KEYWORD_PATTERN with no negation handling at all - the identical
negation-blindness bug checkpoint 98 fixed for estimate_hours()/
estimate_complexity()/estimate_urgency() and checkpoint 102 fixed for
route_task(), never swept to this fourth sibling even though it scans the
same desc_lower with the same keyword-tier shape (checkpoint 97's own
\\b-boundary fix at this exact site covered substring false positives, but
not negation).

Confirmed live before the fix, direct import, no mocking:

    infer_required_skills("This is not a deployment issue - just a "
                           "customer support ticket about a confusing "
                           "error message")
    -> ['deployment', 'customer_service']

"deployment" is a bare word-boundary hit inside the very clause denying
it. Through the real production pipeline (task_executor_v2.decide_on_task
-> agent_decisions.can_execute()'s skill-gap check), this made a department
head escalate work it was fully equipped to do: support_head (expertise
customer_service/problem_solving/documentation, no "deployment", skill
level 3) decided "escalate" on that exact task, reasoning "Missing
expertise in: deployment" - a plain support ticket that explicitly
disclaims being a deployment issue, with no other department even capable
of picking it up, blocked on a requirement the text never actually makes.

Fixed by reusing _is_negated() (already defined above infer_required_skills
in departments.py for route_task() and the three estimate_*() functions) in
infer_required_skills()'s match loop: a skill keyword only counts if it has
at least one non-negated match. Same clause-bounded, 4-word-window,
literal-cue-only scope as the existing fix - no change to word-boundary
matching, the permissive-empty-list default, or SKILL_KEYWORDS itself.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from departments import DepartmentManager
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from task_executor_v2 import DepartmentHeadAgent
from performance import PerformanceAnalytics
from llm_provider import LLMProvider


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_negated_skill_keyword_no_longer_required():
    """The exact live false positive proven above, plus same-shape coverage
    for other SKILL_KEYWORDS entries: a skill explicitly ruled out in the
    text must not end up in required_skills."""
    print_section("TEST 1: a negated skill keyword is no longer inferred")

    cases = [
        ("This is not a deployment issue - just a customer support ticket "
         "about a confusing error message",
         ["customer_service"], "not a deployment issue ... support ticket"),
        ("No budget planning needed, just confirm the existing numbers are right",
         [], "no budget planning needed"),
        ("This isn't a UI problem, the backend logic is wrong",
         [], "isn't a UI problem"),
    ]
    for desc, expected, label in cases:
        got = DepartmentManager.infer_required_skills(desc)
        print(f"  infer_required_skills({label!r}) -> {got!r} (expect {expected!r})")
        assert set(got) == set(expected), f"{label!r}: got {got!r}, expected {expected!r}"

    print("\n  [OK] negated skill keywords are no longer inferred as required")


def test_real_unnegated_inference_still_works():
    """The fix must not regress ordinary, un-negated skill inference -
    including descriptions that genuinely need the same skills the cases
    above negate."""
    print_section("TEST 2: real, un-negated skill inference is unaffected")

    cases = [
        ("Please deploy the new release to production", ["deployment"]),
        ("We need a budget planning session before funding this", ["budget_planning"]),
        ("Fix the broken UI on the signup page", ["ui_design"]),
        ("Write docs for the new API", ["documentation"]),
    ]
    for desc, expected in cases:
        got = DepartmentManager.infer_required_skills(desc)
        print(f"  infer_required_skills({desc!r}) -> {got!r} (expect {expected!r})")
        assert set(got) == set(expected), f"{desc!r}: got {got!r}, expected {expected!r}"

    print("\n  [OK] real, un-negated skill inference is unaffected")


def test_negation_does_not_cross_a_clause_boundary():
    """A negation cue in an earlier clause must not suppress a real,
    un-negated skill keyword in a later clause separated by punctuation -
    same clause-boundary rule _is_negated() already enforces elsewhere."""
    print_section("TEST 3: negation is clause-bounded, not sentence-wide")

    desc = "Not sure about scope, but this definitely needs a deployment this week"
    got = DepartmentManager.infer_required_skills(desc)
    print(f"  infer_required_skills({desc!r}) -> {got!r} (expect ['deployment'], "
          f"'not' must not leak past the comma)")
    assert got == ["deployment"], (
        f"negation leaked across a clause boundary: got {got!r}, expected ['deployment']"
    )

    print("\n  [OK] negation does not cross a clause boundary")


def test_all_negated_description_requires_nothing():
    """When negation suppresses every real match, required_skills must
    fall back to [] - permissive, never a fabricated requirement kept
    alive only by a negated mention."""
    print_section("TEST 4: fully-negated description infers no skill at all")

    desc = "This is not a deployment and not a documentation task either"
    got = DepartmentManager.infer_required_skills(desc)
    print(f"  infer_required_skills({desc!r}) -> {got!r} (expect [])")
    assert got == [], f"got {got!r}, expected [] with every real match negated"

    print("\n  [OK] fully-negated descriptions infer no required skill")


def test_real_pipeline_no_longer_escalates_the_denied_skill():
    """Drive the real production path end to end - DepartmentHeadAgent.
    decide_on_task() -> AgentDecisionEngine.decide() -> can_execute()'s
    skill-gap check - with isolated agent/budget/capacity/metrics state, no
    mocking of infer_required_skills, can_execute, or decide. This is the
    exact confirmed-live repro from the module docstring: support_head must
    now execute the plain support ticket it denies being about deployment,
    instead of escalating on a fabricated 'Missing expertise in: deployment'
    skill gap."""
    print_section("TEST 5: real decide_on_task() pipeline no longer escalates")

    agent_registry = AgentRegistry(data_file="data/test_infer_skills_negation_agents.json")
    budget_manager = BudgetManager(data_file="data/test_infer_skills_negation_budgets.json")
    capacity_manager = CapacityManager(registry=agent_registry)
    analytics = PerformanceAnalytics(data_file="data/test_infer_skills_negation_metrics.json")
    budget_manager.allocate("support", 10000)

    agent = DepartmentHeadAgent(
        "support", llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics,
    )
    state = agent_registry.get(agent.agent_id)
    state.metrics = state.metrics.__class__()
    state.current_workload = 0

    task = ("This is not a deployment issue - just a customer support "
            "ticket about a confusing error message")
    result = agent.decide_on_task(task, estimated_hours=1.0, task_id="ckpt108_repro")
    decision = result["raw_decision"]
    print(f"  decide_on_task({task!r})")
    print(f"    required_skills={result['context'].required_skills}")
    print(f"    decision={decision.decision!r} reasoning={decision.reasoning!r}")

    assert "deployment" not in result["context"].required_skills, (
        "the denied skill must not reach the decision context at all: "
        f"got required_skills={result['context'].required_skills!r}"
    )
    assert decision.decision == "execute", (
        f"expected 'execute' now that the fabricated 'deployment' gap is gone, "
        f"got {decision.decision!r} (reasoning: {decision.reasoning!r})"
    )
    assert "deployment" not in decision.reasoning.lower(), (
        f"reasoning must not still cite the negated skill: {decision.reasoning!r}"
    )

    print("\n  [OK] the real pipeline executes the task instead of escalating "
          "on a skill the task explicitly denies needing")

    import pathlib
    for f in ("data/test_infer_skills_negation_agents.json",
              "data/test_infer_skills_negation_budgets.json",
              "data/test_infer_skills_negation_metrics.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    test_negated_skill_keyword_no_longer_required()
    test_real_unnegated_inference_still_works()
    test_negation_does_not_cross_a_clause_boundary()
    test_all_negated_description_requires_nothing()
    test_real_pipeline_no_longer_escalates_the_denied_skill()

    print_section("ALL TESTS PASSED")
    print("  [OK] infer_required_skills() now recognizes literal, local negation")
    print("       of a skill keyword, without regressing real matches, leaking")
    print("       negation across clause boundaries, or fabricating a skill gap")
    print("       that escalates work the real pipeline could execute.")
