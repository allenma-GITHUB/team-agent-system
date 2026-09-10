#!/usr/bin/env python3
"""
Test budget-gated workflow steps: a step with an estimated_cost now reserves
real money from its department's budget before it can start, confirms that
reservation into spend on approval, and releases it on rejection. A step the
department can't afford blocks instead of proceeding into an approval gate
no one can actually fund.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, WorkflowStatus, StepStatus
from budgets import BudgetManager

# Isolated instead of the shared global budget_manager (WorkflowEngine now
# supports injecting one, the same DI pattern used elsewhere today).
budget_manager = BudgetManager(data_file="data/test_wfb_budgets.json")


def _engine() -> WorkflowEngine:
    return WorkflowEngine(data_file="data/test_workflow_engine.json", budget_manager=budget_manager)


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def make_template(workflow_id: str, step_cost: float, department: str = "qa_wf_dept") -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id=workflow_id,
        name="Budget-gated test workflow",
        description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department=department),
            WorkflowStep(
                step_id="spend", name="Spend Step", owner_department=department,
                depends_on=["intake"], requires_approval=True, estimated_cost=step_cost
            ),
        ]
    )


def test_approval_confirms_reservation_into_spend():
    """Enough budget: the step reserves, then approval converts the hold to real spend."""
    print_section("1. Approved Step Commits Its Reservation")

    department = "qa_wf_ok"
    budget_manager.allocate(department, 10000)

    engine = _engine()
    engine.register_template(make_template("wf_ok", step_cost=4000, department=department))
    instance = engine.create_instance("wf_ok", {})
    engine.start_instance(instance.instance_id)

    step1 = engine.get_next_step(instance.instance_id)
    engine.complete_step(instance.instance_id, step1.step_id, {})

    step2 = engine.get_next_step(instance.instance_id)
    print(f"  Step '{step2.name}' reserved (status: {instance.step_status[step2.step_id].value})")
    assert instance.step_status[step2.step_id] == StepStatus.IN_PROGRESS

    budget = budget_manager.get_budget(department)
    print(f"  Reserved: ${budget.reserved_total():,.2f}, Spent: ${budget.spent:,.2f}")
    assert budget.reserved_total() == 4000 and budget.spent == 0

    engine.approve_step(instance.instance_id, step2.step_id, approved=True)
    print(f"  After approval -> Reserved: ${budget.reserved_total():,.2f}, Spent: ${budget.spent:,.2f}")
    assert budget.reserved_total() == 0 and budget.spent == 4000


def test_rejection_releases_reservation():
    """Rejecting the step should release the hold, not spend it."""
    print_section("2. Rejected Step Releases Its Reservation")

    department = "qa_wf_reject"
    budget_manager.allocate(department, 10000)

    engine = _engine()
    engine.register_template(make_template("wf_reject", step_cost=2500, department=department))
    instance = engine.create_instance("wf_reject", {})
    engine.start_instance(instance.instance_id)

    step1 = engine.get_next_step(instance.instance_id)
    engine.complete_step(instance.instance_id, step1.step_id, {})
    step2 = engine.get_next_step(instance.instance_id)

    budget = budget_manager.get_budget(department)
    print(f"  Reserved before rejection: ${budget.reserved_total():,.2f}")
    assert budget.reserved_total() == 2500

    engine.approve_step(instance.instance_id, step2.step_id, approved=False)
    print(f"  Reserved after rejection: ${budget.reserved_total():,.2f} (released back)")
    assert budget.reserved_total() == 0 and budget.spent == 0
    assert instance.status == WorkflowStatus.PAUSED


def test_unaffordable_step_blocks_workflow():
    """A step costing more than the department can afford should block, not run."""
    print_section("3. Unaffordable Step Blocks the Workflow")

    department = "qa_wf_broke"
    budget_manager.allocate(department, 1000)  # can't cover a $5,000 step

    engine = _engine()
    engine.register_template(make_template("wf_broke", step_cost=5000, department=department))
    instance = engine.create_instance("wf_broke", {})
    engine.start_instance(instance.instance_id)

    step1 = engine.get_next_step(instance.instance_id)
    engine.complete_step(instance.instance_id, step1.step_id, {})

    step2 = engine.get_next_step(instance.instance_id)
    print(f"  get_next_step() returned: {step2} (expected None - blocked)")
    assert step2 is None

    instance = engine.get_instance(instance.instance_id)
    print(f"  Workflow status: {instance.status.value}")
    print(f"  Step status: {instance.step_status['spend'].value}")
    print(f"  Error: {instance.error}")
    assert instance.status == WorkflowStatus.ESCALATED
    assert instance.step_status["spend"] == StepStatus.BLOCKED

    budget = budget_manager.get_budget(department)
    assert budget.reserved_total() == 0 and budget.spent == 0
    print(f"  Budget untouched: reserved=${budget.reserved_total():,.2f}, spent=${budget.spent:,.2f}")

    # Blocked workflows don't silently self-complete
    assert not engine.check_complete(instance.instance_id)


def test_split_owner_and_budget_department():
    """A step can be owned by one department but funded by another's budget."""
    print_section("4. Step Owner and Budget Department Can Differ")

    budget_manager.allocate("qa_wf_finance", 1_000_000)   # who approves - plenty, irrelevant here
    budget_manager.allocate("qa_wf_engineering", 20000)    # who actually funds it

    engine = _engine()
    template = WorkflowTemplate(
        workflow_id="wf_split", name="Split budget test", description="",
        steps=[WorkflowStep(
            step_id="approval", name="Budget Approval", owner_department="qa_wf_finance",
            requires_approval=True, estimated_cost=15000, budget_department="qa_wf_engineering"
        )]
    )
    engine.register_template(template)
    instance = engine.create_instance("wf_split", {})
    engine.start_instance(instance.instance_id)

    step = engine.get_next_step(instance.instance_id)
    print(f"  Step owner: {step.owner_department}, funded by: {step.budget_dept()}")
    assert step.budget_dept() == "qa_wf_engineering"

    finance_budget = budget_manager.get_budget("qa_wf_finance")
    eng_budget = budget_manager.get_budget("qa_wf_engineering")
    print(f"  Finance reserved: ${finance_budget.reserved_total():,.2f} (should be $0)")
    print(f"  Engineering reserved: ${eng_budget.reserved_total():,.2f} (should be $15,000)")
    assert finance_budget.reserved_total() == 0
    assert eng_budget.reserved_total() == 15000

    engine.approve_step(instance.instance_id, step.step_id, approved=True)
    assert eng_budget.spent == 15000


def main():
    print("\n" + "=" * 60)
    print("  BUDGET-GATED WORKFLOW STEPS DEMONSTRATION")
    print("=" * 60)

    test_approval_confirms_reservation_into_spend()
    test_rejection_releases_reservation()
    test_unaffordable_step_blocks_workflow()
    test_split_owner_and_budget_department()

    print("\n" + "=" * 60)
    print("  [OK] All workflow budget-gating tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_workflow_engine.json", "data/test_wfb_budgets.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
