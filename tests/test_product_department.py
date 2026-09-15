#!/usr/bin/env python3
"""
Test the "product" department end-to-end: routing, real declared expertise,
and a route_task() substring-matching bug found and fixed while adding it.

Checkpoint 41's next step was "give every department real declared
expertise in config.json beyond the five built-in heads" - until this
checkpoint, config.json's "departments" dict only ever had five entries
(engineering/design/support/research/sales), and DepartmentHeadAgent's
agent_id is always f"{department}_head" (task_executor_v2.py), so the
skill-gap check and coverage-aware delegate ranking (checkpoints 38-41)
had exactly five real vocabularies to work with - every other department
name got a placeholder expertise_areas=[department] forever.

Proven live before any fix, adding a "product" department with keywords
like "roadmap"/"requirements"/"stakeholder":

    DepartmentManager.route_task(
        "Prioritize the roadmap and gather requirements from stakeholders"
    ) -> "design"

Wrong on two counts: "product" didn't exist as a department at all yet, and
even after adding its keywords, route_task()'s `kw.lower() in desc_lower`
substring check sent the task to "design" anyway, because "requirements"
contains "ui" ("req-UI-rements") and design's keyword list has "ui" in it.
This is the exact class of bug checkpoint 38 already fixed once in
infer_required_skills() (SKILL_KEYWORDS) - "ui" matching inside "build" -
but that fix was scoped to SKILL_KEYWORDS only; route_task()'s own,
separate keyword scan still did naive substring matching.
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


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _product_agent(suffix: str) -> DepartmentHeadAgent:
    registry = AgentRegistry(data_file=f"data/test_product_dept_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_product_dept_budgets_{suffix}.json")
    agent = DepartmentHeadAgent(
        "product", llm_provider=LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_product_dept_metrics_{suffix}.json"),
    )
    budgets.allocate("product", 100000)
    # register() returns the existing state if a previous run left one on
    # disk, and workload/metrics accumulate across runs.
    agent.agent_state.current_workload = 0
    agent.agent_state.metrics = PerformanceMetrics()
    return agent


def test_route_task_finds_the_new_department():
    """The exact repro above: a product-vocabulary task must route to
    "product", not fall through to "engineering" (no matches) or get
    hijacked by another department's substring match."""
    print_section("1. route_task() Routes Product Work To \"product\"")

    dept = DepartmentManager.route_task(
        "Prioritize the roadmap and gather requirements from stakeholders"
    )
    print(f"  route_task() -> {dept}")
    assert dept == "product"


def test_route_task_word_boundary_regression():
    """The bug found while adding "product": "requirements" must not trip
    design's "ui" keyword via substring match. Isolates the exact word from
    the repro above, independent of "product" existing as a department, by
    checking design does NOT win when nothing design-specific is present."""
    print_section("2. route_task() No Longer Substring-Matches \"ui\" Inside \"requirements\"")

    config = DepartmentManager.load_config()
    desc = "gather requirements from stakeholders".lower()
    design_keywords = config["departments"]["design"]["keywords"]
    hits = [kw for kw in design_keywords if kw.lower() in desc]
    print(f"  design keywords present as raw substrings: {hits}")
    assert hits == ["ui"], "this test only proves what it claims if the substring trap is still there to avoid"

    dept = DepartmentManager.route_task("gather requirements from stakeholders")
    print(f"  route_task() -> {dept}")
    assert dept != "design"


def test_existing_departments_still_route_correctly():
    """Guard against the word-boundary fix breaking any of the five
    departments checkpoint 38-41 already depended on."""
    print_section("3. Existing Department Routing Is Unchanged")

    cases = {
        "Fix a backend bug in the API": "engineering",
        "Create a new mockup and visual layout": "design",
        "Help troubleshoot a customer complaint": "support",
        "Analyze the data and write a report with findings": "research",
        "Negotiate a new sales contract with the client": "sales",
    }
    for description, expected in cases.items():
        dept = DepartmentManager.route_task(description)
        print(f"  {description!r} -> {dept}")
        assert dept == expected


def test_product_head_has_real_declared_expertise():
    """config.json's "agents" entry for product_head must be real data, not
    absent - the precondition for infer_required_skills() to ever fire for
    it (task_executor_v2.decide_on_task's has_declared_expertise gate)."""
    print_section("4. product_head Has A Real config.json Entry")

    agent_config = DepartmentManager.get_agent_config("product_head")
    print(f"  get_agent_config('product_head') -> {agent_config}")
    assert agent_config is not None
    assert agent_config["expertise_areas"] == [
        "product_strategy", "roadmap_planning", "requirements_gathering"
    ]

    budget = DepartmentManager.get_monthly_budget("product")
    print(f"  get_monthly_budget('product') -> {budget}")
    assert budget == 28000, "must read the configured budget, not the 10000 placeholder fallback"


def test_infer_required_skills_recognizes_product_vocabulary():
    """The new SKILL_KEYWORDS entries must actually map onto product_head's
    real expertise_areas strings, not just exist as unused dictionary keys."""
    print_section("5. infer_required_skills() Recognizes Product Vocabulary")

    skills = DepartmentManager.infer_required_skills(
        "Prioritize the roadmap and gather requirements from stakeholders"
    )
    print(f"  inferred skills: {skills}")
    assert set(skills) == {"roadmap_planning", "requirements_gathering"}


def test_product_head_executes_matching_work_cleanly():
    """End-to-end through decide_on_task(): a real product_head, built from
    config.json, on genuinely matching work."""
    print_section("6. product_head Executes Matching Work")

    agent = _product_agent("match")
    decision = agent.decide_on_task(
        "Prioritize the roadmap and write user stories based on stakeholder requirements"
    )
    print(f"  required_skills used: {decision['context'].required_skills}")
    print(f"  decision: {decision['decision']}")
    assert decision["context"].required_skills
    assert decision["decision"] == "execute"


def test_synthetic_skill_gap_fires_for_product_vocabulary():
    """Direct AgentDecisionEngine check (same pattern as
    test_delegate_coverage_ranking.py) that the new tags integrate with
    can_execute()'s gap logic. Uses a locally-lowered skill_level - not
    config's real skill_level=4 - because can_execute()'s gap check only
    blocks skill_level < 4 (see test_required_skills.py's note #6);
    product_head's real config profile therefore never demonstrates the
    check firing on its own, exactly like every configured head except
    support_head. This isolates the vocabulary/tagging, not that policy
    choice - whether product_head *should* be skill_level 4 is left to the
    owner, same as every other threshold in config.json."""
    print_section("7. Product Vocabulary Produces A Real, Checkable Skill Gap")

    profile = AgentProfile(
        agent_id="product_head_synthetic", name="Product (synthetic, skill 3)",
        agent_type="ManagerAgent", department="product",
        expertise_areas=["product_strategy", "roadmap_planning", "requirements_gathering"],
        skill_level=3, capabilities=[], constraints=[], max_concurrent_tasks=4,
    )
    registry = AgentRegistry(data_file="data/test_product_dept_synth_agents.json")
    state = registry.register(profile)
    state.current_workload = 0
    state.metrics = PerformanceMetrics()

    engine = AgentDecisionEngine(state, registry=registry)

    matching = DecisionContext(
        task_id="t1", task_type="product",
        required_skills=DepartmentManager.infer_required_skills(
            "Prioritize the roadmap based on stakeholder requirements"),
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    can_do, reason = engine.can_execute(matching)
    print(f"  matching task required_skills={matching.required_skills} -> can_execute={can_do} ({reason})")
    assert can_do

    mismatched = DecisionContext(
        task_id="t2", task_type="product",
        required_skills=DepartmentManager.infer_required_skills(
            "Review the architecture and deploy the new release"),
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    can_do2, reason2 = engine.can_execute(mismatched)
    print(f"  mismatched task required_skills={mismatched.required_skills} -> can_execute={can_do2} ({reason2})")
    assert not can_do2
    assert "Missing expertise" in reason2

    import pathlib
    pathlib.Path("data/test_product_dept_synth_agents.json").unlink(missing_ok=True)


def test_delegate_ranking_can_find_product_head_by_real_skill():
    """find_best_delegate() must be able to surface a product-expertise
    candidate purely from its real expertise_areas - the coverage-aware
    ranking checkpoint 41 built has nothing to reward without a sixth
    department carrying genuine vocabulary to match against."""
    print_section("8. Delegate Ranking Can Route To Product Expertise")

    registry = AgentRegistry(data_file="data/test_product_dept_delegate_agents.json")

    def _profile(agent_id, department, expertise, skill_level=4):
        return AgentProfile(
            agent_id=agent_id, name=agent_id.replace("_", " ").title(),
            agent_type="ManagerAgent", department=department,
            expertise_areas=list(expertise), skill_level=skill_level,
            capabilities=[], constraints=[], max_concurrent_tasks=4,
        )

    delegator = registry.register(_profile("ceo", "leadership", ["strategy"]))
    unrelated = registry.register(_profile("support_head", "support", ["customer_service"]))
    product = registry.register(
        _profile("product_head", "product",
                 ["product_strategy", "roadmap_planning", "requirements_gathering"])
    )
    for state in (delegator, unrelated, product):
        state.current_workload = 0
        state.learned_preferences = {}
        state.metrics = PerformanceMetrics()

    context = DecisionContext(
        task_id="t3", task_type="product",
        required_skills=DepartmentManager.infer_required_skills(
            "Prioritize the roadmap and gather requirements from stakeholders"),
        complexity=0.3, urgency=0.5, estimated_hours=2.0, required_approval_level=1,
    )
    engine = AgentDecisionEngine(delegator, registry=registry)
    delegate = engine.find_best_delegate(context)
    print(f"  required_skills: {context.required_skills}")
    print(f"  find_best_delegate() -> {delegate.profile.agent_id if delegate else None}")
    assert delegate is not None
    assert delegate.profile.agent_id == "product_head"

    import pathlib
    pathlib.Path("data/test_product_dept_delegate_agents.json").unlink(missing_ok=True)


def main():
    print("\n" + "=" * 60)
    print("  PRODUCT DEPARTMENT DEMONSTRATION")
    print("=" * 60)

    for _ in range(2):  # idempotent: each test resets workload/metrics itself
        test_route_task_finds_the_new_department()
        test_route_task_word_boundary_regression()
        test_existing_departments_still_route_correctly()
        test_product_head_has_real_declared_expertise()
        test_infer_required_skills_recognizes_product_vocabulary()
        test_product_head_executes_matching_work_cleanly()
        test_synthetic_skill_gap_fires_for_product_vocabulary()
        test_delegate_ranking_can_find_product_head_by_real_skill()

    print("\n" + "=" * 60)
    print("  [OK] All product department tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for suffix in ["match"]:
        for name in ("agents", "budgets", "metrics"):
            pathlib.Path(f"data/test_product_dept_{name}_{suffix}.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
