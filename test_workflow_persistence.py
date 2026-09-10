#!/usr/bin/env python3
"""
Test that WorkflowEngine instances actually survive across separate
WorkflowEngine objects pointed at the same file - simulating two separate
CLI process invocations, since every main_v2.py command starts a fresh
Python process with no shared memory. Before this, WorkflowEngine kept
everything in a plain in-memory dict with no persistence at all, so any
workflow state would vanish the moment the process exited.
"""
from pathlib import Path

from workflows import (
    WorkflowEngine, WorkflowStep, WorkflowTemplate, WorkflowStatus, StepStatus
)
from budgets import budget_manager

DATA_FILE = "data/test_workflow_persistence.json"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_persist_test", name="Persistence test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_persist"),
            WorkflowStep(step_id="review", name="Review", owner_department="qa_persist",
                         depends_on=["intake"], requires_approval=True),
        ]
    )


def test_instance_survives_a_fresh_engine_pointed_at_the_same_file():
    """A second, brand-new WorkflowEngine (simulating a new process) should
    see the exact instance state the first one left behind."""
    print_section("1. Instance State Survives a Fresh Engine Object")

    Path(DATA_FILE).unlink(missing_ok=True)
    budget_manager.allocate("qa_persist", 10000)

    # "Process 1": create and partially advance a workflow instance
    engine1 = WorkflowEngine(data_file=DATA_FILE)
    engine1.register_template(make_template())
    instance = engine1.create_instance("wf_persist_test", {"title": "Persisted feature"})
    engine1.start_instance(instance.instance_id)
    step1 = engine1.get_next_step(instance.instance_id)
    engine1.complete_step(instance.instance_id, step1.step_id, {"done": True})
    instance_id = instance.instance_id

    # "Process 2": a completely fresh engine, no shared memory with engine1 -
    # only the file. Templates must be re-registered (they're never persisted).
    engine2 = WorkflowEngine(data_file=DATA_FILE)
    engine2.register_template(make_template())

    restored = engine2.get_instance(instance_id)
    print(f"  Restored instance found: {restored is not None}")
    assert restored is not None
    print(f"  Status: {restored.status.value}, input_data: {restored.input_data}")
    assert restored.status == WorkflowStatus.IN_PROGRESS
    assert restored.input_data == {"title": "Persisted feature"}
    assert restored.step_status["intake"] == StepStatus.COMPLETED

    # And it's not just a read-only snapshot - engine2 can keep driving it forward
    step2 = engine2.get_next_step(instance_id)
    print(f"  Next step from the fresh engine: {step2.name if step2 else None}")
    assert step2 is not None and step2.step_id == "review"

    engine2.approve_step(instance_id, "review", approved=True)
    assert engine2.check_complete(instance_id)
    print(f"  Completed via the second engine object entirely")


def test_templates_are_not_persisted():
    """Templates are code-defined and must be re-registered each process -
    a fresh engine shouldn't magically know about a template it was never told about."""
    print_section("2. Templates Are Never Persisted (By Design)")

    Path(DATA_FILE).unlink(missing_ok=True)

    engine1 = WorkflowEngine(data_file=DATA_FILE)
    engine1.register_template(make_template())
    instance = engine1.create_instance("wf_persist_test", {})
    assert instance is not None

    engine2 = WorkflowEngine(data_file=DATA_FILE)  # fresh - no register_template() call
    print(f"  Templates known to a fresh, un-registered engine: {list(engine2.templates.keys())}")
    assert engine2.templates == {}

    # The instance itself is still there...
    assert engine2.get_instance(instance.instance_id) is not None
    # ...but nothing can advance it without its template.
    result = engine2.get_next_step(instance.instance_id)
    print(f"  get_next_step() without a registered template: {result}")
    assert result is None


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW PERSISTENCE ACROSS PROCESSES DEMONSTRATION")
    print("=" * 60)

    test_instance_survives_a_fresh_engine_pointed_at_the_same_file()
    test_templates_are_not_persisted()

    print("\n" + "=" * 60)
    print("  [OK] All workflow-persistence tests passed!")
    print("=" * 60 + "\n")

    Path(DATA_FILE).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
