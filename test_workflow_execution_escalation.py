#!/usr/bin/env python3
"""
Regression test for the workflow half of checkpoint 27's bug: WorkflowEngine.
execute_step() marked a step COMPLETED regardless of what the executor
actually did. Confirmed via a real repro (see DAILY_PROGRESS.md checkpoint
28): draining a department's budget, then running `workflow complete` on a
step owned by that department, marked the step COMPLETED even though its
own step_result said "Escalated, not executed: Insufficient budget...".

Fixed: execute_step() now checks the executor's result for
status == "escalated" and, if so, records the result for visibility but
leaves the step IN_PROGRESS and puts the workflow instance into
WorkflowStatus.ESCALATED with a clear instance.error - instead of calling
complete_step() unconditionally.

Uses a FakeExecutor (same pattern as test_workflow_execution.py) so this
tests the workflow-engine bridge mechanics in isolation from any real
budget/LLM dependency.
"""
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus, WorkflowStatus


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class FakeExecutor:
    """Returns whatever canned result each call is configured to - lets a
    test script a department that "declines" the work exactly like a real
    TaskExecutor.execute() would when budget/capacity/decision-engine says no."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, department: str, description: str):
        self.calls.append((department, description))
        return self.result


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_escalation_test", name="Escalation test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_esc_dept",
                         description="Collect the request"),
            WorkflowStep(step_id="build", name="Build", owner_department="qa_esc_dept",
                         description="Do the work", depends_on=["intake"]),
        ]
    )


def test_execute_step_does_not_complete_an_escalated_step():
    print_section("1. execute_step() Does Not Mark An Escalated Step COMPLETED")

    engine = WorkflowEngine(data_file="data/test_wf_escalation.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_escalation_test", {})
    engine.start_instance(instance.instance_id)

    escalated_result = {
        "department": "qa_esc_dept",
        "analysis": "Escalated, not executed: Insufficient budget: $500.00 requested, $0.00 available",
        "status": "escalated",
        "approved": False,
        "summary": "Qa_Esc_Dept team escalated task after 0.01s: ...",
    }
    executor = FakeExecutor(escalated_result)

    step = engine.get_next_step(instance.instance_id)
    assert step.step_id == "intake"

    ok = engine.execute_step(instance.instance_id, "intake", executor)
    print(f"  execute_step() returned: {ok}")
    assert ok is False

    refreshed = engine.get_instance(instance.instance_id)
    print(f"  step_status: {refreshed.step_status}")
    print(f"  instance.status: {refreshed.status}")
    print(f"  instance.error: {refreshed.error}")

    assert refreshed.step_status["intake"] == StepStatus.IN_PROGRESS  # not COMPLETED
    assert refreshed.status == WorkflowStatus.ESCALATED
    assert "Escalated" in refreshed.error
    assert refreshed.step_results["intake"] == escalated_result  # kept for visibility


def test_get_next_step_keeps_returning_the_escalated_step():
    print_section("2. get_next_step() Keeps Handing Back The Still-In-Progress Step")

    engine = WorkflowEngine(data_file="data/test_wf_escalation.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_escalation_test", {})
    engine.start_instance(instance.instance_id)

    engine.execute_step(instance.instance_id, "intake", FakeExecutor({
        "status": "escalated", "approved": False, "analysis": "no budget"
    }))

    # A caller re-checking status (e.g. `workflow next` again) must not
    # silently see the workflow move past the still-undone step.
    refreshed = engine.get_instance(instance.instance_id)
    refreshed.status = WorkflowStatus.IN_PROGRESS  # operator/CLI re-arms after noticing the issue
    next_step = engine.get_next_step(instance.instance_id)
    print(f"  next_step: {next_step.step_id if next_step else None}")
    assert next_step is not None
    assert next_step.step_id == "intake"


def test_execute_step_still_completes_normally_when_not_escalated():
    print_section("3. execute_step() Still Completes Normally For Real Work")

    engine = WorkflowEngine(data_file="data/test_wf_escalation.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_escalation_test", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor({"status": "completed", "approved": True, "summary": "done"})
    ok = engine.execute_step(instance.instance_id, "intake", executor)
    print(f"  execute_step() returned: {ok}")
    assert ok is True

    refreshed = engine.get_instance(instance.instance_id)
    assert refreshed.step_status["intake"] == StepStatus.COMPLETED
    assert refreshed.status != WorkflowStatus.ESCALATED


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW EXECUTE_STEP ESCALATION REGRESSION TEST")
    print("=" * 60)

    test_execute_step_does_not_complete_an_escalated_step()
    test_get_next_step_keeps_returning_the_escalated_step()
    test_execute_step_still_completes_normally_when_not_escalated()

    print("\n" + "=" * 60)
    print("  [OK] All workflow execute_step escalation tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    pathlib.Path("data/test_wf_escalation.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
