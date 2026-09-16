#!/usr/bin/env python3
"""
Test that WorkflowEngine.approve_step() actually enforces a step's
approval_role, instead of accepting any (or no) approver for any step_id.

Proven live before this fix: approve_step() took no approver argument at
all and unconditionally flipped a step's status to APPROVED regardless of
its declared approval_role, and would do the same to a step that never had
requires_approval=True in the first place - silently marking unexecuted
work as done. Both gaps are pinned here.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus
from budgets import BudgetManager
from agent_state import AgentRegistry, AgentProfile

# Isolated instead of the shared global managers, per project convention -
# this test must never touch data/agent_states.json or data/budgets.json.
budget_manager = BudgetManager(data_file="data/test_war_budgets.json")
agent_registry = AgentRegistry(data_file="data/test_war_agents.json")


def _profile(agent_id: str, agent_type: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id, agent_type=agent_type,
        department="engineering", expertise_areas=[], skill_level=3,
        capabilities=[], constraints=[]
    )


def _register(agent_id: str, agent_type: str):
    state = agent_registry.get(agent_id)
    if state is None:
        agent_registry.register(_profile(agent_id, agent_type))
    else:
        # register() returns an existing state across runs - reset so a
        # prior run's leftovers can't mask a bug this run should catch.
        state.profile.agent_type = agent_type


def _engine() -> WorkflowEngine:
    return WorkflowEngine(
        data_file="data/test_war_engine.json",
        budget_manager=budget_manager,
        agent_state_registry=agent_registry
    )


def _instance_with_role_gated_step(engine: WorkflowEngine, workflow_id: str, role):
    template = WorkflowTemplate(
        workflow_id=workflow_id, name="role gated", description="",
        steps=[WorkflowStep(
            step_id="gate", name="Gate", owner_department="engineering",
            requires_approval=True, approval_role=role
        )]
    )
    engine.register_template(template)
    instance = engine.create_instance(workflow_id, {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)  # advances "gate" to IN_PROGRESS
    return instance


def print_section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'='*60}\n")


def test_no_approver_rejected():
    print_section("1. No approver given -> rejected, step stays pending")
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_no_approver", "ceo")

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=True)
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is False
    assert instance.step_status["gate"] == StepStatus.IN_PROGRESS


def test_wrong_role_rejected():
    print_section("2. Approver registered, but wrong role -> rejected")
    _register("random_eng", "SpecialistAgent")
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_wrong_role", "ceo")

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=True, approver_id="random_eng")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is False
    assert instance.step_status["gate"] == StepStatus.IN_PROGRESS


def test_unregistered_name_matching_role_rejected():
    print_section("3. approver_id spelled like the role, but never registered -> rejected")
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_unregistered", "phantom_approver")

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=True, approver_id="phantom_approver")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is False


def test_exact_agent_id_match_approves():
    print_section("4. Approver IS the named agent (role == agent_id) -> approved")
    _register("ceo", "LeaderAgent")
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_exact_match", "ceo")

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=True, approver_id="ceo")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is True
    assert instance.step_status["gate"] == StepStatus.APPROVED


def test_generic_role_prefix_match_approves():
    print_section("5. Approver holds the role generically (agent_type prefix) -> approved")
    _register("some_manager", "ManagerAgent")
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_generic_match", "manager")

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=True, approver_id="some_manager")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is True
    assert instance.step_status["gate"] == StepStatus.APPROVED


def test_correct_role_can_still_reject():
    print_section("6. A qualified approver can reject, not just approve")
    _register("ceo", "LeaderAgent")
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_reject_role", "ceo")

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=False, approver_id="ceo")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is True
    assert instance.step_status["gate"] == StepStatus.REJECTED


def test_step_without_requires_approval_cannot_be_approved():
    print_section("7. approve_step() refuses a step that never required approval")
    engine = _engine()
    template = WorkflowTemplate(
        workflow_id="wf_no_gate", name="no gate", description="",
        steps=[WorkflowStep(step_id="plain", name="Plain", owner_department="engineering")]
    )
    engine.register_template(template)
    instance = engine.create_instance("wf_no_gate", {})
    engine.start_instance(instance.instance_id)
    engine.get_next_step(instance.instance_id)

    ok, reason = engine.approve_step(instance.instance_id, "plain", approved=True, approver_id="anyone")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is False
    assert instance.step_status.get("plain") != StepStatus.APPROVED


def test_missing_instance_or_step_still_reports_cleanly():
    print_section("8. Unknown instance/step still fails with a reason, not a crash")
    engine = _engine()
    ok, reason = engine.approve_step("no-such-instance", "gate", approved=True, approver_id="ceo")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is False and reason

    instance = _instance_with_role_gated_step(engine, "wf_unknown_step", "ceo")
    ok, reason = engine.approve_step(instance.instance_id, "no-such-step", approved=True, approver_id="ceo")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is False and reason


def test_no_declared_role_accepts_any_named_approver():
    print_section("9. requires_approval=True with no approval_role: documented, not silently swept")
    # Intentional, minimal behavior: with no role declared there is nothing
    # to check an approver against, so any *named* approver is accepted
    # (still requires an identity - see test 1). None of this project's
    # own workflow templates currently leave approval_role unset on an
    # approval-gated step; this pins the fallback rather than leaving it
    # to be rediscovered from scratch.
    engine = _engine()
    instance = _instance_with_role_gated_step(engine, "wf_no_role", None)

    ok, reason = engine.approve_step(instance.instance_id, "gate", approved=True, approver_id="whoever")
    print(f"  ok={ok}, reason={reason!r}")
    assert ok is True
    assert instance.step_status["gate"] == StepStatus.APPROVED


def main():
    test_no_approver_rejected()
    test_wrong_role_rejected()
    test_unregistered_name_matching_role_rejected()
    test_exact_agent_id_match_approves()
    test_generic_role_prefix_match_approves()
    test_correct_role_can_still_reject()
    test_step_without_requires_approval_cannot_be_approved()
    test_missing_instance_or_step_still_reports_cleanly()
    test_no_declared_role_accepts_any_named_approver()
    print("\nAll approval-role tests passed.\n")


if __name__ == "__main__":
    main()
