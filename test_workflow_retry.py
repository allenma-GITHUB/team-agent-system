#!/usr/bin/env python3
"""
Test WorkflowEngine.retry_blocked_step(): a BLOCKED step (department can't
afford its estimated_cost) previously stayed stuck forever - there was no
way to resume a workflow once the department's budget was topped up. This
adds and tests that path.
"""
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, WorkflowStatus, StepStatus
from budgets import budget_manager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def make_template(workflow_id: str, department: str, step_cost: float) -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id=workflow_id,
        name="Retry test workflow",
        description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department=department),
            WorkflowStep(
                step_id="spend", name="Spend Step", owner_department=department,
                depends_on=["intake"], requires_approval=True, estimated_cost=step_cost
            ),
        ]
    )


def test_retry_succeeds_once_budget_is_topped_up():
    """A blocked step should resume once its department can afford it."""
    print_section("1. Retry Succeeds After Budget Top-Up")

    department = "qa_retry_ok"
    budget_manager.allocate(department, 1000)  # can't cover the $5,000 step yet

    engine = WorkflowEngine(data_file="data/test_workflow_engine.json")
    engine.register_template(make_template("wf_retry_ok", department, step_cost=5000))
    instance = engine.create_instance("wf_retry_ok", {})
    engine.start_instance(instance.instance_id)

    step1 = engine.get_next_step(instance.instance_id)
    engine.complete_step(instance.instance_id, step1.step_id, {})

    blocked = engine.get_next_step(instance.instance_id)
    assert blocked is None
    print(f"  Blocked as expected. Status: {instance.status.value}, "
          f"step: {instance.step_status['spend'].value}")
    assert instance.status == WorkflowStatus.ESCALATED

    # Top up the budget - e.g. a new monthly period, or a reallocation
    budget_manager.allocate(department, 10000)

    resumed, reason = engine.retry_blocked_step(instance.instance_id)
    print(f"  Retry after top-up: resumed={resumed} ({reason})")
    assert resumed

    print(f"  Status: {instance.status.value}, step: {instance.step_status['spend'].value}")
    assert instance.status == WorkflowStatus.IN_PROGRESS
    assert instance.step_status["spend"] == StepStatus.IN_PROGRESS
    assert instance.error is None

    budget = budget_manager.get_budget(department)
    print(f"  Reserved after successful retry: ${budget.reserved_total():,.2f}")
    assert budget.reserved_total() == 5000

    # And the step can now be approved normally
    engine.approve_step(instance.instance_id, "spend", approved=True)
    assert budget.spent == 5000


def test_retry_fails_again_if_still_unaffordable():
    """Retrying too soon (budget still too small) should stay blocked, not crash."""
    print_section("2. Retry Fails Again If Still Unaffordable")

    department = "qa_retry_still_broke"
    budget_manager.allocate(department, 100)

    engine = WorkflowEngine(data_file="data/test_workflow_engine.json")
    engine.register_template(make_template("wf_retry_broke", department, step_cost=5000))
    instance = engine.create_instance("wf_retry_broke", {})
    engine.start_instance(instance.instance_id)

    step1 = engine.get_next_step(instance.instance_id)
    engine.complete_step(instance.instance_id, step1.step_id, {})
    engine.get_next_step(instance.instance_id)  # blocks

    resumed, reason = engine.retry_blocked_step(instance.instance_id)
    print(f"  Retry with no top-up: resumed={resumed} ({reason})")
    assert not resumed
    assert instance.status == WorkflowStatus.ESCALATED
    assert instance.step_status["spend"] == StepStatus.BLOCKED


def test_retry_on_non_blocked_workflow_is_a_no_op():
    """Retrying a workflow that isn't blocked should fail cleanly, not silently advance it."""
    print_section("3. Retry On A Non-Blocked Workflow Is A No-Op")

    department = "qa_retry_healthy"
    budget_manager.allocate(department, 100000)

    engine = WorkflowEngine(data_file="data/test_workflow_engine.json")
    engine.register_template(make_template("wf_retry_healthy", department, step_cost=1000))
    instance = engine.create_instance("wf_retry_healthy", {})
    engine.start_instance(instance.instance_id)

    resumed, reason = engine.retry_blocked_step(instance.instance_id)
    print(f"  Retry before anything is blocked: resumed={resumed} ({reason})")
    assert not resumed
    assert reason == "No blocked step to retry"


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW BLOCKED-STEP RETRY DEMONSTRATION")
    print("=" * 60)

    test_retry_succeeds_once_budget_is_topped_up()
    test_retry_fails_again_if_still_unaffordable()
    test_retry_on_non_blocked_workflow_is_a_no_op()

    print("\n" + "=" * 60)
    print("  [OK] All workflow-retry tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
