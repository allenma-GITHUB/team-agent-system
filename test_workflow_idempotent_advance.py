#!/usr/bin/env python3
"""
Test that calling WorkflowEngine.get_next_step() repeatedly on the same
already-in-progress step is a stable no-op, not a corrupting re-reservation.

Real bug found by manual review, not by running an existing test: every
prior test only ever called get_next_step() once per step before
completing/approving it. A second call (e.g. a user just re-checking
`workflow next <instance_id>` without having advanced anything) re-ran the
whole reservation logic for a step that already held one. Since an
existing reservation is already excluded from DepartmentBudget.available(),
the second attempt failed to "re-reserve" the same amount and wrongly
flipped a perfectly fine step to BLOCKED and the workflow to ESCALATED.

Also exercises WorkflowEngine's budget_manager injection (added alongside
this fix) to confirm the probe that found the bug wasn't itself the bug -
without injection, WorkflowEngine silently uses the shared global
BudgetManager, which made an earlier version of this exact check look like
it passed when it hadn't actually been isolated at all.
"""
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, WorkflowStatus, StepStatus
from budgets import BudgetManager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def make_template(step_cost: float, department: str) -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_idempotent", name="Idempotent advance test", description="",
        steps=[WorkflowStep(
            step_id="spend", name="Spend", owner_department=department,
            requires_approval=True, estimated_cost=step_cost
        )]
    )


def test_repeated_get_next_step_does_not_double_reserve():
    """Five repeated calls should leave the reservation exactly as it was
    after the first - not fail, not grow, not shrink."""
    print_section("1. Repeated get_next_step() Does Not Double-Reserve")

    department = "qa_idempotent_ok"
    budgets = BudgetManager(data_file="data/test_wia_budgets.json")
    budgets.allocate(department, 5000)

    engine = WorkflowEngine(data_file="data/test_wia_engine.json", budget_manager=budgets)
    engine.register_template(make_template(step_cost=3000, department=department))
    instance = engine.create_instance("wf_idempotent", {})
    engine.start_instance(instance.instance_id)

    budget = budgets.get_budget(department)
    for i in range(5):
        step = engine.get_next_step(instance.instance_id)
        print(f"  Call {i + 1}: step={step.step_id if step else None}, "
              f"status={instance.status.value}, reserved=${budget.reserved_total():,.2f}")
        assert step is not None and step.step_id == "spend"
        assert instance.status == WorkflowStatus.IN_PROGRESS
        assert instance.step_status["spend"] == StepStatus.IN_PROGRESS
        assert budget.reserved_total() == 3000  # never grows, never falls to 0


def test_step_still_approves_normally_after_repeated_advance_calls():
    """The idempotent advance shouldn't break the normal approve path afterward."""
    print_section("2. Step Still Approves Normally Afterward")

    department = "qa_idempotent_approve"
    budgets = BudgetManager(data_file="data/test_wia_budgets2.json")
    budgets.allocate(department, 5000)

    engine = WorkflowEngine(data_file="data/test_wia_engine2.json", budget_manager=budgets)
    engine.register_template(make_template(step_cost=3000, department=department))
    instance = engine.create_instance("wf_idempotent", {})
    engine.start_instance(instance.instance_id)

    engine.get_next_step(instance.instance_id)
    engine.get_next_step(instance.instance_id)  # the repeated call that used to corrupt things
    engine.get_next_step(instance.instance_id)

    ok = engine.approve_step(instance.instance_id, "spend", approved=True)
    budget = budgets.get_budget(department)
    print(f"  Approved: {ok}, spent: ${budget.spent:,.2f}, reserved: ${budget.reserved_total():,.2f}")
    assert ok
    assert budget.spent == 3000
    assert budget.reserved_total() == 0


def test_workflow_engine_budget_manager_injection_is_actually_isolated():
    """Confirm the injected BudgetManager, not the shared global one, is
    what get_next_step() actually mutates."""
    print_section("3. WorkflowEngine's budget_manager Injection Is Real")

    department = "qa_idempotent_isolated"
    isolated = BudgetManager(data_file="data/test_wia_budgets3.json")
    isolated.allocate(department, 5000)

    from budgets import budget_manager as global_budget_manager
    global_budget_manager.budgets.pop(department, None)  # make sure it's not there globally either

    engine = WorkflowEngine(data_file="data/test_wia_engine3.json", budget_manager=isolated)
    engine.register_template(make_template(step_cost=3000, department=department))
    instance = engine.create_instance("wf_idempotent", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)

    print(f"  Isolated instance reserved: ${isolated.get_budget(department).reserved_total():,.2f}")
    assert isolated.get_budget(department).reserved_total() == 3000

    global_entry = global_budget_manager.get_budget(department)
    print(f"  Global singleton's budget for this department: {global_entry}")
    assert global_entry is None  # never touched


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW IDEMPOTENT-ADVANCE REGRESSION TEST")
    print("=" * 60)

    test_repeated_get_next_step_does_not_double_reserve()
    test_step_still_approves_normally_after_repeated_advance_calls()
    test_workflow_engine_budget_manager_injection_is_actually_isolated()

    print("\n" + "=" * 60)
    print("  [OK] All idempotent-advance tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_wia_budgets.json", "data/test_wia_engine.json",
              "data/test_wia_budgets2.json", "data/test_wia_engine2.json",
              "data/test_wia_budgets3.json", "data/test_wia_engine3.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
