#!/usr/bin/env python3
"""
Regression test: a workflow instance that finishes every one of its steps
through the real production path never transitioned to WorkflowStatus.COMPLETED
on its own - check_complete() is the only code that performs that
transition, and nothing in main_v2.py's CLI or web_server.py's dashboard
ever called it; only tests did.

Confirmed live before this fix, driving a two-step workflow end to end
through get_next_step()/execute_step() - the exact path `workflow next` /
`workflow complete` (and the web dashboard, which reads the same
get_status()) use:

    step_statuses: {'a': 'completed', 'b': 'completed'}   <- every step done
    instance.status reported: in_progress                 <- but never flips
    completed_at: None                                     <- forever

A second, separate entry point had the identical gap: a workflow whose
*last* step requires approval reaches APPROVED via approve_step() directly,
never through complete_step() - so it needed its own fix, not just one.

Fixed: complete_step() and approve_step()'s approved branch each now call
check_complete() themselves, right after the state transition that could
be the one that finishes the workflow.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, StepStatus, WorkflowStatus
from budgets import BudgetManager
from agent_state import AgentRegistry, AgentProfile

# Isolated instead of the shared global managers, per project convention -
# this test must never touch data/agent_states.json or data/budgets.json.
budget_manager = BudgetManager(data_file="data/test_wf_autocomplete_budgets.json")
agent_registry = AgentRegistry(data_file="data/test_wf_autocomplete_agents.json")


def print_section(title: str):
    print(f"\n{'='*60}\n  {title}\n{'='*60}\n")


def _engine(data_file: str) -> WorkflowEngine:
    return WorkflowEngine(
        data_file=data_file, budget_manager=budget_manager,
        agent_state_registry=agent_registry
    )


class FakeExecutor:
    """Always reports a successful, real execution - these tests are about
    the completion bookkeeping, not department decision logic."""

    def execute(self, department, description):
        return {"status": "completed", "summary": "done", "quality_score": 4.0}


def test_two_step_workflow_reaches_completed_through_execute_step_alone():
    print_section("1. Finishing every step via execute_step() auto-completes the instance")
    engine = _engine("data/test_wf_autocomplete_a.json")
    template = WorkflowTemplate(
        workflow_id="wf_auto_a", name="Auto A", description="",
        steps=[
            WorkflowStep(step_id="a", name="A", owner_department="engineering"),
            WorkflowStep(step_id="b", name="B", owner_department="engineering", depends_on=["a"]),
        ]
    )
    engine.register_template(template)
    instance = engine.create_instance("wf_auto_a", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor()
    # Exactly how main_v2.py's `workflow next` + `workflow complete` drive an
    # instance - get_next_step() then execute_step(), nothing more.
    for _ in range(2):
        step = engine.get_next_step(instance.instance_id)
        assert step is not None
        assert engine.execute_step(instance.instance_id, step.step_id, executor)

    status = engine.get_status(instance.instance_id)
    print(f"  status={status['status']}, completed_at={status['completed_at']}")
    assert status["status"] == "completed"
    assert status["completed_at"] is not None
    # get_next_step() itself must now agree there's nothing left to do.
    assert engine.get_next_step(instance.instance_id) is None


def test_completing_an_earlier_step_does_not_prematurely_complete_the_workflow():
    print_section("2. Finishing a non-final step must NOT mark the whole workflow done")
    engine = _engine("data/test_wf_autocomplete_b.json")
    template = WorkflowTemplate(
        workflow_id="wf_auto_b", name="Auto B", description="",
        steps=[
            WorkflowStep(step_id="a", name="A", owner_department="engineering"),
            WorkflowStep(step_id="b", name="B", owner_department="engineering", depends_on=["a"]),
        ]
    )
    engine.register_template(template)
    instance = engine.create_instance("wf_auto_b", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor()
    step_a = engine.get_next_step(instance.instance_id)
    assert engine.execute_step(instance.instance_id, step_a.step_id, executor)

    status = engine.get_status(instance.instance_id)
    print(f"  after step 'a' only: status={status['status']}")
    assert status["status"] == "in_progress"
    assert status["completed_at"] is None


def test_approval_gated_final_step_auto_completes_only_after_approval():
    print_section("3. A last step gated on approval only auto-completes once approved")
    budget_manager.allocate("engineering", 10000)
    state = agent_registry.get("tech_lead")
    if state is None:
        agent_registry.register(AgentProfile(
            agent_id="tech_lead", name="tech_lead", agent_type="TechLeadAgent",
            department="engineering", expertise_areas=[], skill_level=4,
            capabilities=[], constraints=[]
        ))

    engine = _engine("data/test_wf_autocomplete_c.json")
    template = WorkflowTemplate(
        workflow_id="wf_auto_c", name="Auto C", description="",
        steps=[
            WorkflowStep(step_id="a", name="A", owner_department="engineering"),
            WorkflowStep(
                step_id="verify", name="Verify", owner_department="engineering",
                depends_on=["a"], requires_approval=True, approval_role="tech_lead",
                estimated_cost=100,
            ),
        ]
    )
    engine.register_template(template)
    instance = engine.create_instance("wf_auto_c", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor()
    step_a = engine.get_next_step(instance.instance_id)
    assert engine.execute_step(instance.instance_id, step_a.step_id, executor)

    step_verify = engine.get_next_step(instance.instance_id)
    assert step_verify is not None and step_verify.step_id == "verify"
    assert engine.execute_step(instance.instance_id, "verify", executor)

    mid_status = engine.get_status(instance.instance_id)
    print(f"  work done, awaiting approval: status={mid_status['status']}, "
          f"verify={mid_status['step_statuses']['verify']}")
    assert mid_status["status"] == "in_progress"
    assert mid_status["step_statuses"]["verify"] == "awaiting_approval"

    ok, reason = engine.approve_step(instance.instance_id, "verify", approved=True, approver_id="tech_lead")
    assert ok, reason

    final_status = engine.get_status(instance.instance_id)
    print(f"  after approval: status={final_status['status']}, completed_at={final_status['completed_at']}")
    assert final_status["status"] == "completed"
    assert final_status["completed_at"] is not None


def test_rejecting_the_final_step_does_not_complete_the_workflow():
    print_section("4. Rejecting a final approval-gated step must not auto-complete anything")
    budget_manager.allocate("engineering_reject", 10000)

    engine = _engine("data/test_wf_autocomplete_d.json")
    template = WorkflowTemplate(
        workflow_id="wf_auto_d", name="Auto D", description="",
        steps=[
            WorkflowStep(
                step_id="verify", name="Verify", owner_department="engineering_reject",
                requires_approval=True, approval_role="tech_lead", estimated_cost=50,
            ),
        ]
    )
    engine.register_template(template)
    instance = engine.create_instance("wf_auto_d", {})
    engine.start_instance(instance.instance_id)

    executor = FakeExecutor()
    step = engine.get_next_step(instance.instance_id)
    assert engine.execute_step(instance.instance_id, step.step_id, executor)

    ok, reason = engine.approve_step(instance.instance_id, "verify", approved=False, approver_id="tech_lead")
    assert ok, reason

    status = engine.get_status(instance.instance_id)
    print(f"  after rejection: status={status['status']}")
    assert status["status"] == "paused"
    assert status["completed_at"] is None


def main():
    test_two_step_workflow_reaches_completed_through_execute_step_alone()
    test_completing_an_earlier_step_does_not_prematurely_complete_the_workflow()
    test_approval_gated_final_step_auto_completes_only_after_approval()
    test_rejecting_the_final_step_does_not_complete_the_workflow()
    print("\nAll workflow auto-completion tests passed.\n")


if __name__ == "__main__":
    main()
