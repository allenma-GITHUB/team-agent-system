#!/usr/bin/env python3
"""
Checkpoint 91: execute_step()'s escalation branch (workflows.py, the one
that fires when the executor itself declines the work - budget/capacity/
decision-engine inside TaskExecutor/DepartmentHeadAgent, NOT the separate
BLOCKED-on-reservation path get_next_step()/retry_blocked_step() already
handle) left two things permanently wrong, confirmed live first with the
real CLI in an isolated temp directory before any fix:

1. A step escalated via execute_step() stayed at whatever step_status it
   already had, which is PENDING unless something had already called
   get_next_step() on it first. Neither of this method's two real callers
   - main_v2.py's `workflow complete <id> <step>` and the dashboard's
   matching "Complete Step" button, both of which name a step_id and call
   execute_step() directly - ever do that. So `workflow status` showed the
   escalated step reading "pending", identical to a step genuinely never
   attempted, right above a detail line (its own stored result's summary)
   describing the real attempt that just happened.

2. Once instance.status flips to ESCALATED, nothing - not complete_step(),
   not check_complete() (gated on status == IN_PROGRESS), not
   retry_blocked_step() (only resets status for the *other* BLOCKED path) -
   ever set it back, no matter what happened next. Confirmed live: drain
   engineering's budget so a bug_fix instance's 'fix' step escalates via
   execute_step(), top the budget back up, call `workflow complete <id>
   fix` again directly (the only path that can retry this kind of
   escalation at all) - it genuinely re-executes and completes, but
   instance.status stayed "escalated" forever after, even once every
   remaining step was completed the same way. get_next_step() (and so
   `workflow next`) refuses to run for a non-IN_PROGRESS instance, so the
   workflow could never be driven forward through its normal path again,
   and a workflow that finished every single step still reported status
   "escalated", not "completed".

Fixed: execute_step()'s escalation branch now sets step_status[step_id] to
IN_PROGRESS and current_step to step_id explicitly, instead of trusting
whatever they already happened to be. complete_step() now resets
instance.status from ESCALATED back to IN_PROGRESS whenever a step
actually completes - the same "real progress happened" reasoning already
used there to clear a stale instance.error, extended to the status it was
set alongside.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus, WorkflowStatus


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class FakeExecutor:
    """Returns canned results in sequence, one per call - lets a test script
    a department escalating on its first attempt and succeeding on a later
    retry, exactly like a real TaskExecutor.execute() would once the real
    blocker (budget, in production) is resolved in between."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def execute(self, department: str, description: str, task_id=None):
        self.calls.append((department, description))
        return self.results.pop(0)


def make_template(workflow_id: str) -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id=workflow_id, name="Recovery test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_rec_dept",
                         description="Collect the request"),
            WorkflowStep(step_id="build", name="Build", owner_department="qa_rec_dept",
                         description="Do the work", depends_on=["intake"]),
        ]
    )


ESCALATED_RESULT = {
    "department": "qa_rec_dept",
    "analysis": "Escalated, not executed: Insufficient budget: $500.00 requested, $0.00 available",
    "status": "escalated",
    "approved": False,
    "summary": "Qa_Rec_Dept team escalated task after 0.00s: Escalated, not executed: ...",
}

COMPLETED_RESULT = {
    "department": "qa_rec_dept",
    "analysis": "did the work",
    "status": "completed",
    "approved": True,
    "summary": "Qa_Rec_Dept team completed task in 0.00s",
}


def test_escalated_step_reads_in_progress_not_pending():
    print_section("1. An Escalated Step Reads IN_PROGRESS, Not The Stale PENDING It Started At")

    engine = WorkflowEngine(data_file="data/test_wf_escalated_recovery.json")
    engine.register_template(make_template("wf_rec_status"))
    instance = engine.create_instance("wf_rec_status", {})
    engine.start_instance(instance.instance_id)

    # No get_next_step() call first - this is exactly the path
    # `workflow complete <id> <step>` and the dashboard's "Complete Step"
    # button both take: name a step_id, call execute_step() directly.
    assert instance.step_status["intake"] == StepStatus.PENDING

    ok = engine.execute_step(instance.instance_id, "intake", FakeExecutor([ESCALATED_RESULT]))
    assert ok is False

    refreshed = engine.get_instance(instance.instance_id)
    print(f"  step_status['intake']: {refreshed.step_status['intake']}")
    print(f"  current_step: {refreshed.current_step}")
    assert refreshed.step_status["intake"] == StepStatus.IN_PROGRESS
    assert refreshed.current_step == "intake"
    assert refreshed.status == WorkflowStatus.ESCALATED


def test_successful_retry_clears_escalated_instance_status():
    print_section("2. A Successful Retry Resets instance.status Off ESCALATED")

    engine = WorkflowEngine(data_file="data/test_wf_escalated_recovery.json")
    engine.register_template(make_template("wf_rec_retry"))
    instance = engine.create_instance("wf_rec_retry", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor([ESCALATED_RESULT, COMPLETED_RESULT])

    ok1 = engine.execute_step(instance.instance_id, "intake", executor)
    assert ok1 is False
    refreshed = engine.get_instance(instance.instance_id)
    assert refreshed.status == WorkflowStatus.ESCALATED
    print(f"  After escalation: status={refreshed.status.value}")

    # The real blocker is gone now (in production: budget topped up) - the
    # same step_id is re-attempted directly, same as `workflow complete
    # <id> intake` run a second time.
    ok2 = engine.execute_step(instance.instance_id, "intake", executor)
    assert ok2 is True

    refreshed = engine.get_instance(instance.instance_id)
    print(f"  After successful retry: status={refreshed.status.value}, "
          f"step_status={refreshed.step_status['intake'].value}")
    assert refreshed.status == WorkflowStatus.IN_PROGRESS
    assert refreshed.step_status["intake"] == StepStatus.COMPLETED
    assert refreshed.error is None


def test_workflow_reaches_completed_after_recovering_from_escalation():
    print_section("3. A Workflow That Finishes Every Step After Recovering Reports COMPLETED")

    engine = WorkflowEngine(data_file="data/test_wf_escalated_recovery.json")
    engine.register_template(make_template("wf_rec_full"))
    instance = engine.create_instance("wf_rec_full", {})
    engine.start_instance(instance.instance_id)

    intake_executor = FakeExecutor([COMPLETED_RESULT])
    assert engine.execute_step(instance.instance_id, "intake", intake_executor) is True

    build_executor = FakeExecutor([ESCALATED_RESULT, COMPLETED_RESULT])
    assert engine.execute_step(instance.instance_id, "build", build_executor) is False
    stuck = engine.get_instance(instance.instance_id)
    assert stuck.status == WorkflowStatus.ESCALATED
    # Before the fix, get_next_step() would refuse forever from here on -
    # the instance is no longer IN_PROGRESS.
    assert engine.get_next_step(instance.instance_id) is None

    assert engine.execute_step(instance.instance_id, "build", build_executor) is True

    final = engine.get_instance(instance.instance_id)
    print(f"  Final status: {final.status.value}")
    print(f"  step_status: {{k: v.value for k, v in final.step_status.items()}}")
    assert final.status == WorkflowStatus.COMPLETED
    assert final.completed_at is not None


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW ESCALATED-STEP RECOVERY REGRESSION TEST")
    print("=" * 60)

    test_escalated_step_reads_in_progress_not_pending()
    test_successful_retry_clears_escalated_instance_status()
    test_workflow_reaches_completed_after_recovering_from_escalation()

    print("\n" + "=" * 60)
    print("  [OK] All workflow escalated-step recovery tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    pathlib.Path("data/test_wf_escalated_recovery.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
