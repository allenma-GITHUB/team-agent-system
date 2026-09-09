#!/usr/bin/env python3
"""
Test script demonstrating workflow orchestration.
Shows multi-step workflows with approval gates and dependencies.
"""
from workflows import (
    workflow_engine, create_feature_request_workflow,
    create_bug_fix_workflow, WorkflowStatus, StepStatus
)


def test_feature_workflow():
    """Demonstrate feature request workflow."""
    print("\n" + "="*60)
    print("  Feature Request Workflow Test")
    print("="*60 + "\n")

    # Register workflow template
    template = create_feature_request_workflow()
    workflow_engine.register_template(template)
    print(f"[OK] Registered workflow: {template.name}")
    print(f"    Steps: {len(template.steps)}")
    for step in template.steps:
        approval = " (requires approval)" if step.requires_approval else ""
        print(f"      - {step.name}{approval}")

    # Create instance
    input_data = {
        "title": "Add dark mode support",
        "priority": "high",
        "requester": "customer_acme"
    }
    instance = workflow_engine.create_instance("feature_request", input_data)
    print(f"\n[OK] Created workflow instance: {instance.instance_id}")
    print(f"    Input: {input_data}")

    # Start workflow
    workflow_engine.start_instance(instance.instance_id)
    print(f"[OK] Started workflow, status: {instance.status.value}")

    # Execute first step
    step1 = workflow_engine.get_next_step(instance.instance_id)
    print(f"\n[Step 1] {step1.name} ({step1.owner_department})")
    workflow_engine.complete_step(instance.instance_id, step1.step_id, {"requirements": "documented"})
    print(f"  [OK] Completed")

    # Execute design step (with approval)
    step2 = workflow_engine.get_next_step(instance.instance_id)
    print(f"\n[Step 2] {step2.name} ({step2.owner_department})")
    print(f"  [WAIT] Requires approval from: {step2.approval_role}")
    workflow_engine.complete_step(instance.instance_id, step2.step_id, {"design": "mockups created"})
    # Approve the step
    workflow_engine.approve_step(instance.instance_id, step2.step_id, approved=True)
    print(f"  [OK] Approved by design lead")

    # Get status
    status = workflow_engine.get_status(instance.instance_id)
    print(f"\n[Status] Progress: {status['progress']:.0%}")
    print(f"  Current step: {status['current_step']}")

    print("\n" + "="*60)


def test_bug_fix_workflow():
    """Demonstrate bug fix workflow (faster process)."""
    print("\n" + "="*60)
    print("  Bug Fix Workflow Test")
    print("="*60 + "\n")

    # Register bug fix workflow
    template = create_bug_fix_workflow()
    workflow_engine.register_template(template)
    print(f"[OK] Registered workflow: {template.name}")
    print(f"    Steps: {len(template.steps)}")

    # Create and start instance
    input_data = {"bug_id": "CRITICAL-2401", "severity": "critical"}
    instance = workflow_engine.create_instance("bug_fix", input_data)
    workflow_engine.start_instance(instance.instance_id)
    print(f"\n[OK] Started bug fix workflow")

    # Quick execution
    for i in range(3):
        step = workflow_engine.get_next_step(instance.instance_id)
        if not step:
            break
        print(f"\n[Step {i+1}] {step.name}")
        workflow_engine.complete_step(instance.instance_id, step.step_id, {"done": True})
        if step.requires_approval:
            workflow_engine.approve_step(instance.instance_id, step.step_id, approved=True)
            print(f"  [OK] Approved")
        else:
            print(f"  [OK] Completed")

    # Check if complete
    if workflow_engine.check_complete(instance.instance_id):
        instance = workflow_engine.get_instance(instance.instance_id)
        print(f"\n[COMPLETE] Bug fix workflow finished")
        print(f"  Status: {instance.status.value}")
        print(f"  Completed at: {instance.completed_at}")

    print("\n" + "="*60)


def test_workflow_rejection():
    """Demonstrate workflow pause on rejection."""
    print("\n" + "="*60)
    print("  Workflow Rejection Test")
    print("="*60 + "\n")

    # Create feature workflow
    template = create_feature_request_workflow()
    workflow_engine.register_template(template)

    instance = workflow_engine.create_instance("feature_request", {"title": "Test"})
    workflow_engine.start_instance(instance.instance_id)

    # Go through first steps
    step1 = workflow_engine.get_next_step(instance.instance_id)
    workflow_engine.complete_step(instance.instance_id, step1.step_id, {})

    step2 = workflow_engine.get_next_step(instance.instance_id)
    workflow_engine.complete_step(instance.instance_id, step2.step_id, {})

    # Reject the step
    print(f"[Step] {step2.name}")
    workflow_engine.approve_step(instance.instance_id, step2.step_id, approved=False)
    instance = workflow_engine.get_instance(instance.instance_id)
    print(f"[REJECTED] Workflow paused")
    print(f"  Status: {instance.status.value}")
    print(f"  Error: {instance.error}")

    print("\n" + "="*60)


def main():
    """Run all workflow tests."""
    test_feature_workflow()
    test_bug_fix_workflow()
    test_workflow_rejection()

    print("\n" + "="*60)
    print("  All workflow tests complete!")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
