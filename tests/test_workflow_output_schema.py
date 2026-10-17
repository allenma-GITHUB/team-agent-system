#!/usr/bin/env python3
"""
Test WorkflowStep.output_schema: declared since this project's first
commit (per DAILY_PROGRESS.md checkpoint 68's Next Steps), defaulted to
{}, and never read by anything - a step's real result could be any shape
at all and nothing would notice. This gives it a real producer
(WorkflowStep.validate_output()) and a real consumer (WorkflowEngine.
complete_step(), the one place every caller - execute_step() or a direct
complete_step() call - ends up), matching this project's "never report
unfinished work as finished" convention: a step whose result doesn't
match its own declared schema can't be marked COMPLETED/AWAITING_APPROVAL.

input_schema is deliberately left untouched - unlike output_schema, there
is no existing notion of a step's "input" in this engine today (a step's
executor call carries only its name/description, never the previous
step's result or the workflow's input_data), so wiring it up would mean
inventing that data-flow design rather than reading something already
computed. Recorded as an open question in DAILY_PROGRESS.md rather than
guessed at here.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus, WorkflowStatus
from budgets import BudgetManager
from agent_state import AgentRegistry


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class FakeExecutor:
    """Returns a caller-supplied canned result per call, in order - lets
    each test control exactly what a step's "real" result looks like
    without needing an LLM, a budget, or an agent registry."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, department: str, description: str):
        self.calls.append((department, description))
        return self.results.pop(0)


def make_template(output_schema=None) -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_schema_test", name="Output schema test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_schema_dept",
                         description="Collect the request"),
            WorkflowStep(step_id="build", name="Build", owner_department="qa_schema_dept",
                         description="Do the work", depends_on=["intake"],
                         output_schema=output_schema or {}),
        ]
    )


def test_validate_output_is_a_no_op_when_schema_is_empty():
    """The {} default (every built-in template today) must accept any
    result at all - this is what keeps the fix dormant for existing
    templates."""
    print_section("1. validate_output() With No Declared Schema")

    step = WorkflowStep(step_id="s", name="S", owner_department="d")
    print(f"  output_schema: {step.output_schema}")
    assert step.validate_output({}) == []
    assert step.validate_output({"anything": object()}) == []


def test_validate_output_flags_a_missing_field():
    print_section("2. validate_output() Flags A Missing Field")

    step = WorkflowStep(step_id="s", name="S", owner_department="d",
                         output_schema={"severity": "str"})
    violations = step.validate_output({"other": "value"})
    print(f"  violations: {violations}")
    assert violations == ["missing field 'severity' (expected str)"]


def test_validate_output_flags_a_wrong_type():
    print_section("3. validate_output() Flags A Wrong Type")

    step = WorkflowStep(step_id="s", name="S", owner_department="d",
                         output_schema={"retry_count": "int"})
    violations = step.validate_output({"retry_count": "three"})
    print(f"  violations: {violations}")
    assert violations == ["field 'retry_count' is str, expected int"]


def test_validate_output_rejects_bool_as_int():
    """isinstance(True, int) is True in plain Python - the validator must
    not let a boolean quietly pass as a declared "int" field."""
    print_section("4. validate_output() Does Not Let bool Pass As int")

    step = WorkflowStep(step_id="s", name="S", owner_department="d",
                         output_schema={"count": "int"})
    violations = step.validate_output({"count": True})
    print(f"  violations: {violations}")
    assert violations == ["field 'count' is bool, expected int"]
    assert step.validate_output({"count": 3}) == []


def test_validate_output_accepts_int_or_float_for_number():
    print_section("5. validate_output() 'number' Accepts int Or float")

    step = WorkflowStep(step_id="s", name="S", owner_department="d",
                         output_schema={"cost": "number"})
    assert step.validate_output({"cost": 5}) == []
    assert step.validate_output({"cost": 5.5}) == []
    assert step.validate_output({"cost": "5"}) == ["field 'cost' is str, expected number"]


def test_validate_output_unknown_type_name_is_presence_only():
    """A schema typo (a type name this validator doesn't recognize)
    should not make every real call look invalid - fall back to checking
    the field exists, nothing more."""
    print_section("6. validate_output() Falls Back To Presence-Only")

    step = WorkflowStep(step_id="s", name="S", owner_department="d",
                         output_schema={"widget": "frobnicator"})
    assert step.validate_output({"widget": 12345}) == []
    assert step.validate_output({}) == ["missing field 'widget' (expected frobnicator)"]


def test_complete_step_rejects_a_result_that_fails_the_schema():
    """The actual wiring: complete_step() must not advance a step to
    COMPLETED when its result fails the step's own declared schema -
    this project's signature bug (unfinished work recorded as done)."""
    print_section("7. complete_step() Rejects A Schema-Failing Result")

    engine = WorkflowEngine(data_file="data/test_wf_schema1.json",
                             budget_manager=BudgetManager(data_file="data/test_wf_schema_budget1.json"),
                             agent_state_registry=AgentRegistry(data_file="data/test_wf_schema_agents1.json"))
    engine.register_template(make_template(output_schema={"severity": "str"}))
    instance = engine.create_instance("wf_schema_test", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)  # advances to "intake"
    engine.complete_step(instance.instance_id, "intake", {})
    engine.get_next_step(instance.instance_id)  # advances to "build"

    ok = engine.complete_step(instance.instance_id, "build", {"summary": "done, but no severity"})

    instance = engine.get_instance(instance.instance_id)
    print(f"  complete_step() returned: {ok}")
    print(f"  step_status['build']: {instance.step_status['build']}")
    print(f"  instance.error: {instance.error}")
    assert ok is False
    assert instance.step_status["build"] != StepStatus.COMPLETED
    assert "severity" in instance.error
    assert "qa_schema_dept" not in instance.error  # sanity: didn't leak an unrelated field


def test_complete_step_accepts_a_result_that_satisfies_the_schema():
    """The positive case - a result with the right shape completes
    exactly as it would have before this fix existed."""
    print_section("8. complete_step() Accepts A Schema-Satisfying Result")

    engine = WorkflowEngine(data_file="data/test_wf_schema2.json",
                             budget_manager=BudgetManager(data_file="data/test_wf_schema_budget2.json"),
                             agent_state_registry=AgentRegistry(data_file="data/test_wf_schema_agents2.json"))
    engine.register_template(make_template(output_schema={"severity": "str"}))
    instance = engine.create_instance("wf_schema_test", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)
    engine.complete_step(instance.instance_id, "intake", {})
    engine.get_next_step(instance.instance_id)

    ok = engine.complete_step(instance.instance_id, "build", {"severity": "high"})

    instance = engine.get_instance(instance.instance_id)
    print(f"  complete_step() returned: {ok}")
    print(f"  step_status['build']: {instance.step_status['build']}")
    assert ok is True
    assert instance.step_status["build"] == StepStatus.COMPLETED
    assert instance.status == WorkflowStatus.COMPLETED  # last step done -> whole workflow done


def test_execute_step_end_to_end_blocks_on_a_schema_violation():
    """The real caller path: execute_step() runs the (fake) executor,
    gets a real result back, and must refuse to advance the workflow past
    a step whose result fails its own schema - not just complete_step()
    called directly."""
    print_section("9. execute_step() End-To-End Blocks On A Schema Violation")

    engine = WorkflowEngine(data_file="data/test_wf_schema3.json",
                             budget_manager=BudgetManager(data_file="data/test_wf_schema_budget3.json"),
                             agent_state_registry=AgentRegistry(data_file="data/test_wf_schema_agents3.json"))
    engine.register_template(make_template(output_schema={"severity": "str"}))
    instance = engine.create_instance("wf_schema_test", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)
    executor = FakeExecutor([{}])
    engine.execute_step(instance.instance_id, "intake", executor)
    engine.get_next_step(instance.instance_id)

    executor = FakeExecutor([{"summary": "built it, forgot severity"}])
    ok = engine.execute_step(instance.instance_id, "build", executor)

    instance = engine.get_instance(instance.instance_id)
    print(f"  execute_step() returned: {ok}")
    print(f"  step_status['build']: {instance.step_status['build']}")
    print(f"  workflow status: {instance.status}")
    assert ok is False
    assert instance.step_status["build"] == StepStatus.IN_PROGRESS  # never advanced
    assert instance.status != WorkflowStatus.COMPLETED


def test_built_in_templates_declare_no_output_schema_and_stay_dormant():
    """Confirms the dormant-by-default claim this checkpoint rests on:
    neither shipped template declares an output_schema on any step, so
    this fix changes nothing about how they run today."""
    print_section("10. Built-In Templates Are Unaffected (output_schema Dormant)")

    from workflows import create_feature_request_workflow, create_bug_fix_workflow

    for template in (create_feature_request_workflow(), create_bug_fix_workflow()):
        for step in template.steps:
            print(f"  {template.workflow_id}.{step.step_id}.output_schema = {step.output_schema}")
            assert step.output_schema == {}
            assert step.validate_output({"literally": "anything"}) == []


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOWSTEP.OUTPUT_SCHEMA WIRING DEMONSTRATION")
    print("=" * 60)

    test_validate_output_is_a_no_op_when_schema_is_empty()
    test_validate_output_flags_a_missing_field()
    test_validate_output_flags_a_wrong_type()
    test_validate_output_rejects_bool_as_int()
    test_validate_output_accepts_int_or_float_for_number()
    test_validate_output_unknown_type_name_is_presence_only()
    test_complete_step_rejects_a_result_that_fails_the_schema()
    test_complete_step_accepts_a_result_that_satisfies_the_schema()
    test_execute_step_end_to_end_blocks_on_a_schema_violation()
    test_built_in_templates_declare_no_output_schema_and_stay_dormant()

    print("\n" + "=" * 60)
    print("  [OK] All output_schema tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_wf_schema1.json", "data/test_wf_schema_budget1.json", "data/test_wf_schema_agents1.json",
              "data/test_wf_schema2.json", "data/test_wf_schema_budget2.json", "data/test_wf_schema_agents2.json",
              "data/test_wf_schema3.json", "data/test_wf_schema_budget3.json", "data/test_wf_schema_agents3.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
