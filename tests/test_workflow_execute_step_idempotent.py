#!/usr/bin/env python3
"""
Regression test for a real double-billing bug found while probing workflow
CLI idempotency (the same class of bug fixed for get_next_step() in an
earlier checkpoint, here found in execute_step() instead): calling
`workflow complete <instance> <step>` a second time on an already-completed
step re-ran the department's task through the executor all over again -
double budget spend, double workload/performance-metric increments, all
silently, with `complete_step()` just overwriting step_results with the
new (billed-again) result.

Confirmed via a real repro: `workflow start bug_fix`, `workflow next`,
`workflow complete <iid> triage` twice - support's budget.spent went from
$180 (correct, one execution) to $360 (billed twice for one step).

Fixed: execute_step() now returns True immediately, without touching the
executor, when the step is already COMPLETED or APPROVED - the same
"idempotent no-op for already-done work" pattern used elsewhere in this
codebase (get_next_step()'s already-in-progress short-circuit).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus, WorkflowStatus


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class CountingExecutor:
    """Counts real executions so a test can assert the executor was (or
    wasn't) actually invoked again, not just that the reported result
    looks the same."""

    def __init__(self):
        self.calls = 0

    def execute(self, department: str, description: str):
        self.calls += 1
        return {
            "status": "completed", "approved": True,
            "summary": f"real execution #{self.calls}", "department": department,
        }


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_idempotent_test", name="Idempotent execute_step test", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_idem_dept",
                         description="Collect the request"),
        ]
    )


def test_execute_step_does_not_re_execute_an_already_completed_step():
    print_section("1. execute_step() Is Idempotent - No Double Execution/Billing")

    engine = WorkflowEngine(data_file="data/test_wf_idempotent.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_idempotent_test", {})
    engine.start_instance(instance.instance_id)

    executor = CountingExecutor()

    ok1 = engine.execute_step(instance.instance_id, "intake", executor)
    ok2 = engine.execute_step(instance.instance_id, "intake", executor)
    ok3 = engine.execute_step(instance.instance_id, "intake", executor)

    print(f"  calls: {ok1}, {ok2}, {ok3} -> executor invoked {executor.calls} time(s)")
    assert (ok1, ok2, ok3) == (True, True, True)
    assert executor.calls == 1, f"expected exactly 1 real execution, got {executor.calls}"

    refreshed = engine.get_instance(instance.instance_id)
    assert refreshed.step_status["intake"] == StepStatus.COMPLETED
    assert refreshed.step_results["intake"]["summary"] == "real execution #1"


def test_execute_step_does_not_re_execute_an_approved_step():
    print_section("2. execute_step() Also Skips An Already-APPROVED Step")

    engine = WorkflowEngine(data_file="data/test_wf_idempotent.json")
    engine.register_template(make_template())
    instance = engine.create_instance("wf_idempotent_test", {})
    engine.start_instance(instance.instance_id)
    instance.step_status["intake"] = StepStatus.APPROVED  # simulate an approval-gated step

    executor = CountingExecutor()
    ok = engine.execute_step(instance.instance_id, "intake", executor)

    print(f"  execute_step() on an APPROVED step returned: {ok}, executor invoked {executor.calls} time(s)")
    assert ok is True
    assert executor.calls == 0


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW EXECUTE_STEP IDEMPOTENCY REGRESSION TEST")
    print("=" * 60)

    test_execute_step_does_not_re_execute_an_already_completed_step()
    test_execute_step_does_not_re_execute_an_approved_step()

    print("\n" + "=" * 60)
    print("  [OK] All execute_step idempotency tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    pathlib.Path("data/test_wf_idempotent.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
