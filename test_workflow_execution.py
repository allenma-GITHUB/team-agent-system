#!/usr/bin/env python3
"""
Test WorkflowEngine.execute_step(): a step is now actually run through an
executor and completed with the real result, instead of requiring a caller
to hand-type a placeholder ({"completed_via": "cli"}) for work nobody
actually did. Uses a fake executor here (no real LLM/budget dependency) to
test the bridge mechanics in isolation; the real bridge to
task_executor_v2.TaskExecutor is validated separately via the CLI.
"""
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class FakeExecutor:
    """Records what it was asked to execute; returns a canned result -
    stands in for task_executor_v2.TaskExecutor without needing an LLM,
    a budget, or an agent registry."""

    def __init__(self):
        self.calls = []

    def execute(self, department: str, description: str):
        self.calls.append((department, description))
        return {"summary": f"{department} handled it", "department": department}


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_exec_test", name="Execution test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_exec_dept",
                         description="Collect the request"),
            WorkflowStep(step_id="build", name="Build", owner_department="qa_exec_dept",
                         description="Do the work", depends_on=["intake"]),
        ]
    )


def test_execute_step_calls_the_executor_with_the_right_department_and_description():
    """The executor should receive the step's own department and a
    description built from the step's name/description, not a placeholder."""
    print_section("1. execute_step() Calls The Executor Correctly")

    engine = WorkflowEngine(data_file="data/test_wf_exec.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_exec_test", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)  # advances to "intake"

    executor = FakeExecutor()
    ok = engine.execute_step(instance.instance_id, "intake", executor)

    print(f"  execute_step() returned: {ok}")
    print(f"  Executor was called with: {executor.calls}")
    assert ok
    assert executor.calls == [("qa_exec_dept", "Intake: Collect the request")]


def test_execute_step_records_the_real_result_not_a_placeholder():
    """The step's stored result should be the executor's actual return
    value, not {"completed_via": "cli"} or any other fixed placeholder."""
    print_section("2. execute_step() Records The Real Result")

    engine = WorkflowEngine(data_file="data/test_wf_exec2.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_exec_test", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)

    executor = FakeExecutor()
    engine.execute_step(instance.instance_id, "intake", executor)

    instance = engine.get_instance(instance.instance_id)
    print(f"  Stored result: {instance.step_results['intake']}")
    assert instance.step_results["intake"] == {"summary": "qa_exec_dept handled it",
                                                 "department": "qa_exec_dept"}
    assert instance.step_status["intake"] == StepStatus.COMPLETED


def test_execute_step_returns_false_for_an_unknown_step_or_instance():
    """A bad instance_id or step_id shouldn't crash - just report failure,
    and the executor should never be called for a step that doesn't exist."""
    print_section("3. execute_step() Fails Cleanly For Unknown Instance/Step")

    engine = WorkflowEngine(data_file="data/test_wf_exec3.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_exec_test", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor()

    assert engine.execute_step("no_such_instance", "intake", executor) is False
    assert engine.execute_step(instance.instance_id, "no_such_step", executor) is False
    print(f"  Both correctly returned False; executor calls made: {len(executor.calls)}")
    assert executor.calls == []


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW STEP EXECUTION BRIDGE DEMONSTRATION")
    print("=" * 60)

    test_execute_step_calls_the_executor_with_the_right_department_and_description()
    test_execute_step_records_the_real_result_not_a_placeholder()
    test_execute_step_returns_false_for_an_unknown_step_or_instance()

    print("\n" + "=" * 60)
    print("  [OK] All workflow-execution tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_wf_exec.json", "data/test_wf_exec2.json", "data/test_wf_exec3.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
