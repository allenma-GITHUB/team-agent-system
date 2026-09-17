#!/usr/bin/env python3
"""
Test the "finance" department end-to-end: routing, real declared expertise,
and that the feature_request workflow's own budget_approval step - the
concrete reason this department exists - actually routes correctly now.

Checkpoint 43's own Next Steps named this directly: create_feature_request_
workflow()'s budget_approval step has owner_department="finance", but
config.json had no "finance" entry at all - no keywords, no monthly_budget,
no "finance_head" agent profile. Proven live before any fix, using the
step's own real description:

    DepartmentManager.route_task("Approve budget allocation") -> "engineering"
    DepartmentManager.get_agent_config("finance_head") -> None
    DepartmentManager.get_monthly_budget("finance") -> 10000  (generic fallback)

Wrong on all three counts: "Approve budget allocation" has nothing to do
with engineering, execute_step() would build a DepartmentHeadAgent for
"finance" with the placeholder expertise_areas=["finance"] (task_executor_v2.
DepartmentHeadAgent.__init__), and the seeded department budget was the
generic fallback rather than any deliberate number - exactly the class of
defect checkpoint 42 already fixed once for "product" (same fallback path,
same DepartmentHeadAgent.__init__ code), now demonstrated for "finance".
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_decisions import AgentDecisionEngine, DecisionContext
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from departments import DepartmentManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent
from workflows import create_feature_request_workflow


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _finance_agent(suffix: str) -> DepartmentHeadAgent:
    registry = AgentRegistry(data_file=f"data/test_finance_dept_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_finance_dept_budgets_{suffix}.json")
    agent = DepartmentHeadAgent(
        "finance", llm_provider=LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_finance_dept_metrics_{suffix}.json"),
    )
    budgets.allocate("finance", 100000)
    # register() returns the existing state if a previous run left one on
    # disk, and workload/metrics accumulate across runs.
    agent.agent_state.current_workload = 0
    agent.agent_state.metrics = PerformanceMetrics()
    return agent


def test_real_workflow_step_now_routes_to_finance():
    """The exact repro above, using the actual budget_approval step's own
    description from create_feature_request_workflow(), not a synthetic
    stand-in."""
    print_section('1. The Real budget_approval Step Description Routes To "finance"')

    template = create_feature_request_workflow()
    step = next(s for s in template.steps if s.step_id == "budget_approval")
    assert step.owner_department == "finance"

    dept = DepartmentManager.route_task(step.description)
    print(f"  route_task({step.description!r}) -> {dept}")
    assert dept == "finance"


def test_route_task_no_false_substring_hits_from_other_departments():
    """Guard against the exact word-boundary trap checkpoint 42 found for
    "product" ("requirements" containing "ui"): finance's own keywords must
    not accidentally appear as raw substrings of other departments' text,
    and vice versa, on the description that matters."""
    print_section("2. No Substring Collisions Between finance And Other Departments")

    config = DepartmentManager.load_config()
    desc = "approve budget allocation".lower()
    for dept_name, dept_config in config["departments"].items():
        if dept_name == "finance":
            continue
        hits = [kw for kw in dept_config["keywords"] if kw.lower() in desc]
        print(f"  {dept_name} raw substring hits in {desc!r}: {hits}")
        assert hits == [], f"{dept_name} keywords collide with finance's own routing text"


def test_existing_departments_still_route_correctly():
    """Guard against the new department stealing routing from any of the
    six departments checkpoints 38-42 already depended on."""
    print_section("3. Existing Department Routing Is Unchanged")

    cases = {
        "Fix a backend bug in the API": "engineering",
        "Create a new mockup and visual layout": "design",
        "Help troubleshoot a customer complaint": "support",
        "Analyze the data and write a report with findings": "research",
        "Negotiate a new sales contract with the client": "sales",
        "Prioritize the roadmap and gather requirements from stakeholders": "product",
    }
    for description, expected in cases.items():
        dept = DepartmentManager.route_task(description)
        print(f"  {description!r} -> {dept}")
        assert dept == expected


def test_finance_head_has_real_declared_expertise():
    """config.json's "agents" entry for finance_head must be real data, not
    absent - the precondition for infer_required_skills() to ever fire for
    it (task_executor_v2.decide_on_task's has_declared_expertise gate)."""
    print_section("4. finance_head Has A Real config.json Entry")

    agent_config = DepartmentManager.get_agent_config("finance_head")
    print(f"  get_agent_config('finance_head') -> {agent_config}")
    assert agent_config is not None
    assert agent_config["expertise_areas"] == [
        "budget_planning", "financial_analysis", "cost_estimation"
    ]

    budget = DepartmentManager.get_monthly_budget("finance")
    print(f"  get_monthly_budget('finance') -> {budget}")
    assert budget == 20000, "must read the configured budget, not the 10000 placeholder fallback"


def test_infer_required_skills_recognizes_finance_vocabulary():
    """The new SKILL_KEYWORDS entries must actually map onto finance_head's
    real expertise_areas strings, not just exist as unused dictionary keys."""
    print_section("5. infer_required_skills() Recognizes Finance Vocabulary")

    skills = DepartmentManager.infer_required_skills(
        "Approve the budget and review financial cost of the expense"
    )
    print(f"  inferred skills: {skills}")
    assert set(skills) == {"budget_planning", "financial_analysis", "cost_estimation"}


def test_finance_head_executes_matching_work_cleanly():
    """End-to-end through decide_on_task(): a real finance_head, built from
    config.json, on genuinely matching work - the same shape as the
    budget_approval step's real task description."""
    print_section("6. finance_head Executes Matching Work")

    agent = _finance_agent("match")
    decision = agent.decide_on_task("Approve budget allocation")
    print(f"  required_skills used: {decision['context'].required_skills}")
    print(f"  decision: {decision['decision']}")
    assert decision["context"].required_skills
    assert decision["decision"] == "execute"


def test_synthetic_skill_gap_fires_for_finance_vocabulary():
    """Direct AgentDecisionEngine check (same pattern as
    test_delegate_coverage_ranking.py) that the new tags integrate with
    can_execute()'s gap logic. Uses a locally-lowered skill_level - not
    config's real skill_level=4 - because can_execute()'s gap check only
    blocks skill_level < 4 (see test_required_skills.py's note #6); this
    isolates the vocabulary/tagging, not any policy choice about what
    finance_head's real skill_level should be."""
    print_section("7. Finance Vocabulary Produces A Real, Checkable Skill Gap")

    profile = AgentProfile(
        agent_id="finance_head_synthetic", name="Finance (synthetic, skill 3)",
        agent_type="ManagerAgent", department="finance",
        expertise_areas=["budget_planning", "financial_analysis", "cost_estimation"],
        skill_level=3, capabilities=[], constraints=[], max_concurrent_tasks=4,
    )
    registry = AgentRegistry(data_file="data/test_finance_dept_synth_agents.json")
    state = registry.register(profile)
    state.current_workload = 0
    state.metrics = PerformanceMetrics()

    engine = AgentDecisionEngine(state, registry=registry)

    matching = DecisionContext(
        task_id="t1", task_type="finance",
        required_skills=DepartmentManager.infer_required_skills("Approve budget allocation"),
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    can_do, reason = engine.can_execute(matching)
    print(f"  matching task required_skills={matching.required_skills} -> can_execute={can_do} ({reason})")
    assert can_do

    mismatched = DecisionContext(
        task_id="t2", task_type="finance",
        required_skills=DepartmentManager.infer_required_skills(
            "Review the architecture and deploy the new release"),
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    can_do2, reason2 = engine.can_execute(mismatched)
    print(f"  mismatched task required_skills={mismatched.required_skills} -> can_execute={can_do2} ({reason2})")
    assert not can_do2
    assert "Missing expertise" in reason2

    import pathlib
    pathlib.Path("data/test_finance_dept_synth_agents.json").unlink(missing_ok=True)


def test_delegate_ranking_can_find_finance_head_by_real_skill():
    """find_best_delegate() must be able to surface a finance-expertise
    candidate purely from its real expertise_areas."""
    print_section("8. Delegate Ranking Can Route To Finance Expertise")

    registry = AgentRegistry(data_file="data/test_finance_dept_delegate_agents.json")

    def _profile(agent_id, department, expertise, skill_level=4):
        return AgentProfile(
            agent_id=agent_id, name=agent_id.replace("_", " ").title(),
            agent_type="ManagerAgent", department=department,
            expertise_areas=list(expertise), skill_level=skill_level,
            capabilities=[], constraints=[], max_concurrent_tasks=4,
        )

    delegator = registry.register(_profile("ceo", "leadership", ["strategy"]))
    unrelated = registry.register(_profile("support_head", "support", ["customer_service"]))
    finance = registry.register(
        _profile("finance_head", "finance",
                 ["budget_planning", "financial_analysis", "cost_estimation"])
    )
    for state in (delegator, unrelated, finance):
        state.current_workload = 0
        state.learned_preferences = {}
        state.metrics = PerformanceMetrics()

    context = DecisionContext(
        task_id="t3", task_type="finance",
        required_skills=DepartmentManager.infer_required_skills("Approve budget allocation"),
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills: {context.required_skills}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")
    assert delegate is not None
    assert delegate.profile.agent_id == "finance_head"

    import pathlib
    pathlib.Path("data/test_finance_dept_delegate_agents.json").unlink(missing_ok=True)


def main():
    print("\n" + "=" * 60)
    print("  FINANCE DEPARTMENT DEMONSTRATION")
    print("=" * 60)

    for _ in range(2):  # idempotent: each test resets workload/metrics itself
        test_real_workflow_step_now_routes_to_finance()
        test_route_task_no_false_substring_hits_from_other_departments()
        test_existing_departments_still_route_correctly()
        test_finance_head_has_real_declared_expertise()
        test_infer_required_skills_recognizes_finance_vocabulary()
        test_finance_head_executes_matching_work_cleanly()
        test_synthetic_skill_gap_fires_for_finance_vocabulary()
        test_delegate_ranking_can_find_finance_head_by_real_skill()

    print("\n" + "=" * 60)
    print("  [OK] All finance department tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for suffix in ["match"]:
        for name in ("agents", "budgets", "metrics"):
            pathlib.Path(f"data/test_finance_dept_{name}_{suffix}.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
