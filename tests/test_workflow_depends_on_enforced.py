#!/usr/bin/env python3
"""
Checkpoint 55's own Next Steps named this as the next session's first
priority: execute_step()/complete_step() looked a step up by step_id and
ran it, full stop - WorkflowStep.depends_on was never consulted outside
get_next_step()'s own internal walk. Any caller naming a step_id directly
(exactly what `workflow complete <instance> <step_id>` and the equivalent
execute_step() call always do) could execute and complete any step in any
order, including a later step whose approval gate was never reached.

Confirmed live before this fix, on the stock bug_fix template
(triage -> fix -> verify[approval_role=tech_lead, $500] -> deploy):
execute_step(instance, "deploy", exe) with triage/fix/verify all still
PENDING returned True and marked 'deploy' COMPLETED outright - skipping
past verify's approval gate entirely, not just around it the way
checkpoint 55's fix (AWAITING_APPROVAL) already closed.

Fixed: both execute_step() (before the executor ever runs, so a blocked
step costs the department nothing) and complete_step() (the authoritative
state transition, for any caller that hands in a result directly) now
check every depends_on entry is COMPLETED or APPROVED first.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, create_bug_fix_workflow, StepStatus, WorkflowStatus
from budgets import BudgetManager
from agent_state import AgentRegistry

budget_manager = BudgetManager(data_file="data/test_wdoe_budgets.json")
agent_registry = AgentRegistry(data_file="data/test_wdoe_agents.json")


class CountingExecutor:
    """Counts real executions so a test can assert the executor was never
    invoked for a step that was correctly blocked - not just that the
    step's status stayed unchanged."""

    def __init__(self):
        self.calls = []

    def execute(self, department: str, description: str):
        self.calls.append((department, description))
        return {"status": "completed", "summary": f"did {description}"}


def _engine() -> WorkflowEngine:
    engine = WorkflowEngine(
        data_file="data/test_wdoe_engine.json",
        budget_manager=budget_manager,
        agent_state_registry=agent_registry,
    )
    engine.register_template(create_bug_fix_workflow())
    budget_manager.allocate("engineering", 10000)  # allocate(), not ensure_allocated() - see project convention
    return engine


def print_section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'='*60}\n")


def test_execute_step_refuses_a_step_whose_dependency_never_ran():
    print_section("1. execute_step() refuses 'deploy' with nothing else done")
    engine = _engine()
    exe = CountingExecutor()
    instance = engine.create_instance("bug_fix", {"title": "skip-ahead"})
    engine.start_instance(instance.instance_id)

    ok = engine.execute_step(instance.instance_id, "deploy", exe)
    print(f"  ok={ok}, executor calls={len(exe.calls)}, error={instance.error!r}")
    assert ok is False
    assert exe.calls == [], "the department must not be charged for an unreachable step"
    assert instance.step_status["deploy"] == StepStatus.PENDING
    assert "verify" in instance.error


def test_execute_step_refuses_a_step_whose_dependency_is_only_awaiting_approval():
    print_section("2. execute_step() refuses 'deploy' while 'verify' is only AWAITING_APPROVAL")
    engine = _engine()
    exe = CountingExecutor()
    instance = engine.create_instance("bug_fix", {"title": "gate-not-cleared"})
    engine.start_instance(instance.instance_id)

    for _ in range(2):  # triage, fix
        step = engine.get_next_step(instance.instance_id)
        assert engine.execute_step(instance.instance_id, step.step_id, exe)

    step = engine.get_next_step(instance.instance_id)
    assert step.step_id == "verify"
    assert engine.execute_step(instance.instance_id, step.step_id, exe)
    assert instance.step_status["verify"] == StepStatus.AWAITING_APPROVAL  # work done, not yet approved

    calls_before = len(exe.calls)
    ok = engine.execute_step(instance.instance_id, "deploy", exe)
    print(f"  ok={ok}, executor calls after={len(exe.calls)}, error={instance.error!r}")
    assert ok is False
    assert len(exe.calls) == calls_before, "an unapproved gate must not let a later step run either"
    assert instance.step_status["deploy"] == StepStatus.PENDING


def test_complete_step_also_refuses_a_hand_typed_result_out_of_order():
    print_section("3. complete_step() itself refuses a direct call naming a later step")
    engine = _engine()
    instance = engine.create_instance("bug_fix", {"title": "direct-call"})
    engine.start_instance(instance.instance_id)

    ok = engine.complete_step(instance.instance_id, "verify", {"summary": "hand-typed"})
    print(f"  ok={ok}, error={instance.error!r}")
    assert ok is False
    assert instance.step_status["verify"] == StepStatus.PENDING
    assert "fix" in instance.error


def test_in_order_execution_is_unaffected():
    print_section("4. The normal in-order path through get_next_step() still works end to end")
    engine = _engine()
    exe = CountingExecutor()
    instance = engine.create_instance("bug_fix", {"title": "happy-path"})
    engine.start_instance(instance.instance_id)

    for _ in range(2):  # triage, fix
        step = engine.get_next_step(instance.instance_id)
        assert engine.execute_step(instance.instance_id, step.step_id, exe)

    step = engine.get_next_step(instance.instance_id)
    assert step.step_id == "verify"
    assert engine.execute_step(instance.instance_id, step.step_id, exe)
    assert instance.step_status["verify"] == StepStatus.AWAITING_APPROVAL

    ok, reason = engine.approve_step(instance.instance_id, "verify", True, approver_id="tech_lead_bootstrap")
    # No registered tech_lead in this isolated registry - approval itself isn't
    # this test's concern (checkpoint 55 already covers it); fall back to
    # directly setting APPROVED so this test only exercises depends_on.
    if not ok:
        instance.step_status["verify"] = StepStatus.APPROVED

    step = engine.get_next_step(instance.instance_id)
    assert step.step_id == "deploy"
    ok = engine.execute_step(instance.instance_id, step.step_id, exe)
    print(f"  deploy: ok={ok}, step_status={ {k: v.value for k, v in instance.step_status.items()} }")
    assert ok is True
    assert instance.step_status["deploy"] == StepStatus.COMPLETED
    assert engine.check_complete(instance.instance_id) is True


def test_the_blocked_error_does_not_outlive_real_progress():
    """A blocked out-of-order attempt sets instance.error; once the
    workflow is subsequently driven correctly, that error must not keep
    showing on an instance that isn't blocked anymore - introduced by this
    same fix (error used to only ever be set by a real escalation, which
    also flips status to ESCALATED so it read as current; this path leaves
    status alone, so a stale message would otherwise linger forever)."""
    print_section("5. A cleared blockage doesn't leave a stale error behind")
    engine = _engine()
    exe = CountingExecutor()
    instance = engine.create_instance("bug_fix", {"title": "stale-error-check"})
    engine.start_instance(instance.instance_id)

    assert engine.execute_step(instance.instance_id, "deploy", exe) is False
    assert instance.error is not None
    print(f"  error after blocked attempt: {instance.error!r}")

    step = engine.get_next_step(instance.instance_id)
    assert step.step_id == "triage"
    assert engine.execute_step(instance.instance_id, step.step_id, exe) is True
    print(f"  error after real progress: {instance.error!r}")
    assert instance.error is None


def main():
    test_execute_step_refuses_a_step_whose_dependency_never_ran()
    test_execute_step_refuses_a_step_whose_dependency_is_only_awaiting_approval()
    test_complete_step_also_refuses_a_hand_typed_result_out_of_order()
    test_in_order_execution_is_unaffected()
    test_the_blocked_error_does_not_outlive_real_progress()
    print("\nAll depends_on enforcement tests passed.\n")


if __name__ == "__main__":
    main()
