#!/usr/bin/env python3
"""
Test that an approval-gated step cannot be counted as "done" - by
get_next_step(), check_complete(), or WorkflowInstance.get_progress() -
until approve_step() has actually been called, and that its budget
reservation is neither spent nor silently dropped before that happens.

Proven live before this fix: complete_step()/execute_step() set a step
straight to StepStatus.COMPLETED regardless of requires_approval, and
get_next_step()/check_complete()/get_progress() all treated COMPLETED and
APPROVED as interchangeable "done" states. The result: an entire
bug_fix workflow instance (triage -> fix -> verify -> deploy), with
"verify" gated behind approval_role="tech_lead" and a $500 estimated_cost,
reached check_complete() == True and status "completed" with approve_step()
never called once - while the $500 sat in the engineering budget's
`reserved` dict forever, neither spent nor released. This is this
project's own named signature bug (work recorded as done when it wasn't),
applied to the one place - an approval gate - requires_approval exists to
protect.

The fix introduces StepStatus.AWAITING_APPROVAL as the outcome of
completing a requires_approval step's work, distinct from COMPLETED (which
now means "done, and didn't need anyone's sign-off"). Only approve_step()
can move an AWAITING_APPROVAL step to APPROVED (or REJECTED).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, create_bug_fix_workflow, StepStatus
from budgets import BudgetManager
from agent_state import AgentRegistry, AgentProfile

# Isolated instead of the shared global managers, per project convention.
budget_manager = BudgetManager(data_file="data/test_wagc_budgets.json")
agent_registry = AgentRegistry(data_file="data/test_wagc_agents.json")


class FakeExecutor:
    """Always "succeeds" - the point here is the approval gate, not department
    routing/decision-engine behavior, which other tests already cover."""
    def execute(self, department, description):
        return {"status": "completed", "summary": f"did {description}"}


def _register_tech_lead():
    state = agent_registry.get("tech_lead")
    if state is None:
        agent_registry.register(AgentProfile(
            agent_id="tech_lead", name="tech_lead", agent_type="TechLeadAgent",
            department="engineering", expertise_areas=[], skill_level=4,
            capabilities=[], constraints=[]
        ))


def _engine() -> WorkflowEngine:
    engine = WorkflowEngine(
        data_file="data/test_wagc_engine.json",
        budget_manager=budget_manager,
        agent_state_registry=agent_registry
    )
    engine.register_template(create_bug_fix_workflow())
    # allocate(), not ensure_allocated(): a prior run's `spent`/`reserved` on
    # this same department name would otherwise survive into this run (per
    # project convention - BudgetManager persists to disk) and mask exactly
    # the kind of regression this file exists to catch.
    budget_manager.allocate("engineering", 10000)
    return engine


def _drive_to_gate(engine: WorkflowEngine, exe: FakeExecutor):
    """Create a fresh bug_fix instance and execute every step up to (and
    including) the approval-gated "verify" step, without ever approving it."""
    instance = engine.create_instance("bug_fix", {"title": "Crash on save"})
    engine.start_instance(instance.instance_id)
    for _ in range(3):  # triage, fix, verify - deploy depends on verify so it can't be reached yet
        step = engine.get_next_step(instance.instance_id)
        assert step is not None
        engine.execute_step(instance.instance_id, step.step_id, exe)
    return instance


def print_section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'='*60}\n")


def test_executing_a_gated_step_does_not_complete_it():
    print_section("1. Doing the work on a gated step leaves it AWAITING_APPROVAL, not COMPLETED")
    engine = _engine()
    exe = FakeExecutor()
    instance = _drive_to_gate(engine, exe)

    statuses = {k: v.value for k, v in instance.step_status.items()}
    print(f"  step_status: {statuses}")
    assert instance.step_status["triage"] == StepStatus.COMPLETED
    assert instance.step_status["fix"] == StepStatus.COMPLETED
    assert instance.step_status["verify"] == StepStatus.AWAITING_APPROVAL


def test_check_complete_is_false_until_approved():
    print_section("2. check_complete() stays False with a gate unapproved, even though every step ran")
    engine = _engine()
    exe = FakeExecutor()
    instance = _drive_to_gate(engine, exe)

    result = engine.check_complete(instance.instance_id)
    print(f"  check_complete() = {result}, status = {instance.status.value}")
    assert result is False
    assert instance.status.value == "in_progress"


def test_get_next_step_does_not_skip_past_an_unapproved_gate():
    print_section("3. get_next_step() keeps reporting the gated step, never jumps to 'deploy'")
    engine = _engine()
    exe = FakeExecutor()
    instance = _drive_to_gate(engine, exe)

    next_step = engine.get_next_step(instance.instance_id)
    print(f"  next_step after the gate: {next_step.step_id if next_step else None}")
    assert next_step is not None
    assert next_step.step_id == "verify"
    assert instance.step_status["deploy"] == StepStatus.PENDING


def test_progress_does_not_report_the_gated_step_as_done():
    print_section("4. get_progress() doesn't count an unapproved gate toward completion")
    engine = _engine()
    exe = FakeExecutor()
    instance = _drive_to_gate(engine, exe)

    progress = instance.get_progress()
    print(f"  progress = {progress}")
    # 2 of 4 steps (triage, fix) genuinely done; verify/deploy are not.
    assert progress == 0.5


def test_budget_reservation_survives_until_approval_decides_it():
    print_section("5. The $500 hold for 'verify' is neither spent nor dropped while unapproved")
    engine = _engine()
    exe = FakeExecutor()
    instance = _drive_to_gate(engine, exe)

    budget = budget_manager.get_budget("engineering")
    reference = engine._budget_reference(instance.instance_id, "verify")
    print(f"  spent={budget.spent}, reserved={budget.reserved}")
    assert budget.spent == 0.0
    assert budget.reserved.get(reference) == 500


def test_approval_unblocks_completion_and_commits_the_spend():
    print_section("6. Once approve_step() actually runs, the workflow completes and spend is real")
    engine = _engine()
    exe = FakeExecutor()
    _register_tech_lead()
    instance = _drive_to_gate(engine, exe)

    ok, reason = engine.approve_step(instance.instance_id, "verify", True, approver_id="tech_lead")
    print(f"  approve_step: ok={ok}, reason={reason!r}")
    assert ok is True
    assert instance.step_status["verify"] == StepStatus.APPROVED

    # Now "deploy" becomes reachable and the workflow can finish for real.
    step = engine.get_next_step(instance.instance_id)
    assert step is not None and step.step_id == "deploy"
    engine.execute_step(instance.instance_id, step.step_id, exe)

    result = engine.check_complete(instance.instance_id)
    print(f"  check_complete() = {result}, status = {instance.status.value}")
    assert result is True
    assert instance.status.value == "completed"

    budget = budget_manager.get_budget("engineering")
    reference = engine._budget_reference(instance.instance_id, "verify")
    print(f"  spent={budget.spent}, reserved={budget.reserved}")
    assert budget.spent == 500.0
    assert reference not in budget.reserved


def test_calling_execute_step_again_on_an_awaiting_step_does_not_redo_the_work():
    print_section("7. execute_step() is idempotent on an already-AWAITING_APPROVAL step")
    engine = _engine()

    calls = []

    class CountingExecutor:
        def execute(self, department, description):
            calls.append(description)
            return {"status": "completed", "summary": "ran"}

    exe = CountingExecutor()
    instance = _drive_to_gate(engine, exe)
    calls_before = len(calls)

    ok = engine.execute_step(instance.instance_id, "verify", exe)
    print(f"  second execute_step() on 'verify': ok={ok}, calls before={calls_before}, after={len(calls)}")
    assert ok is True
    assert len(calls) == calls_before  # no re-execution, no double billing

    # get_next_step() also must not re-reserve the budget hold a second time.
    engine.get_next_step(instance.instance_id)
    budget = budget_manager.get_budget("engineering")
    reference = engine._budget_reference(instance.instance_id, "verify")
    assert budget.reserved.get(reference) == 500  # unchanged, not doubled or errored


def main():
    test_executing_a_gated_step_does_not_complete_it()
    test_check_complete_is_false_until_approved()
    test_get_next_step_does_not_skip_past_an_unapproved_gate()
    test_progress_does_not_report_the_gated_step_as_done()
    test_budget_reservation_survives_until_approval_decides_it()
    test_approval_unblocks_completion_and_commits_the_spend()
    test_calling_execute_step_again_on_an_awaiting_step_does_not_redo_the_work()
    print("\nAll approval-gate-blocks-completion tests passed.\n")


if __name__ == "__main__":
    main()
