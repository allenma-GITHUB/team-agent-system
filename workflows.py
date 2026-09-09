"""
Workflow Engine - Coordinates multi-step tasks across agents and departments.
Enables complex business processes like feature requests, bug fixes, and reviews.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from enum import Enum
import json
import time
from datetime import datetime


class WorkflowStatus(Enum):
    """Workflow execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


class StepStatus(Enum):
    """Individual step status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class WorkflowStep:
    """Single step in a workflow."""
    step_id: str
    name: str
    owner_department: str  # Which department executes
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)  # Step IDs this depends on
    requires_approval: bool = False
    approval_role: Optional[str] = None  # e.g., "manager", "ceo"
    timeout_minutes: int = 60
    retry_on_failure: bool = True
    max_retries: int = 2

    def __hash__(self):
        return hash(self.step_id)


@dataclass
class WorkflowInstance:
    """Running instance of a workflow."""
    workflow_id: str
    workflow_name: str
    instance_id: str
    input_data: Dict[str, Any]
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_step: Optional[str] = None
    step_results: Dict[str, Any] = field(default_factory=dict)
    step_status: Dict[str, StepStatus] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None

    def is_complete(self) -> bool:
        """Check if workflow is complete."""
        return self.status in [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED]

    def get_progress(self) -> float:
        """Get completion percentage (0-1)."""
        total = len(self.step_status)
        if total == 0:
            return 0.0
        completed = sum(1 for s in self.step_status.values() if s in [StepStatus.COMPLETED, StepStatus.APPROVED])
        return completed / total


@dataclass
class WorkflowTemplate:
    """Template for recurring workflows."""
    workflow_id: str
    name: str
    description: str
    steps: List[WorkflowStep] = field(default_factory=list)
    version: int = 1
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def create_instance(self, input_data: Dict[str, Any]) -> WorkflowInstance:
        """Create a new instance of this workflow."""
        instance_id = f"{self.workflow_id}_{int(time.time())}_{id(input_data) % 10000}"
        return WorkflowInstance(
            workflow_id=self.workflow_id,
            workflow_name=self.name,
            instance_id=instance_id,
            input_data=input_data,
            step_status={step.step_id: StepStatus.PENDING for step in self.steps}
        )

    def get_next_step(self, completed_steps: set) -> Optional[WorkflowStep]:
        """Get next executable step based on dependencies."""
        for step in self.steps:
            # Check if already done
            if step.step_id in completed_steps:
                continue

            # Check if dependencies are met
            if all(dep in completed_steps for dep in step.depends_on):
                return step

        return None

    def get_step_by_id(self, step_id: str) -> Optional[WorkflowStep]:
        """Find step by ID."""
        return next((s for s in self.steps if s.step_id == step_id), None)


class WorkflowEngine:
    """Orchestrates workflow execution."""

    def __init__(self):
        self.templates: Dict[str, WorkflowTemplate] = {}
        self.instances: Dict[str, WorkflowInstance] = {}

    def register_template(self, template: WorkflowTemplate) -> None:
        """Register a workflow template."""
        self.templates[template.workflow_id] = template

    def create_instance(self, workflow_id: str, input_data: Dict[str, Any]) -> Optional[WorkflowInstance]:
        """Create workflow instance from template."""
        template = self.templates.get(workflow_id)
        if not template:
            return None

        instance = template.create_instance(input_data)
        self.instances[instance.instance_id] = instance
        return instance

    def start_instance(self, instance_id: str) -> bool:
        """Start workflow execution."""
        instance = self.instances.get(instance_id)
        if not instance or instance.status != WorkflowStatus.PENDING:
            return False

        instance.status = WorkflowStatus.IN_PROGRESS
        instance.started_at = datetime.utcnow().isoformat()
        return True

    def complete_step(self, instance_id: str, step_id: str, result: Dict[str, Any]) -> bool:
        """Mark a step as complete with results."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        instance.step_results[step_id] = result
        instance.step_status[step_id] = StepStatus.COMPLETED
        return True

    def approve_step(self, instance_id: str, step_id: str, approved: bool) -> bool:
        """Approve/reject a step requiring approval."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        if approved:
            instance.step_status[step_id] = StepStatus.APPROVED
        else:
            instance.step_status[step_id] = StepStatus.REJECTED
            instance.status = WorkflowStatus.PAUSED
            instance.error = f"Step {step_id} rejected"

        return True

    def get_next_step(self, instance_id: str) -> Optional[WorkflowStep]:
        """Get next step to execute."""
        instance = self.instances.get(instance_id)
        if not instance or instance.status != WorkflowStatus.IN_PROGRESS:
            return None

        template = self.templates.get(instance.workflow_id)
        if not template:
            return None

        completed = {
            step_id for step_id, status in instance.step_status.items()
            if status in [StepStatus.COMPLETED, StepStatus.APPROVED]
        }

        next_step = template.get_next_step(completed)
        if next_step:
            instance.current_step = next_step.step_id
            instance.step_status[next_step.step_id] = StepStatus.IN_PROGRESS

        return next_step

    def check_complete(self, instance_id: str) -> bool:
        """Check if workflow is complete."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        if instance.status == WorkflowStatus.IN_PROGRESS:
            # Check if all steps are complete
            all_done = all(
                status in [StepStatus.COMPLETED, StepStatus.APPROVED]
                for status in instance.step_status.values()
            )

            if all_done:
                instance.status = WorkflowStatus.COMPLETED
                instance.completed_at = datetime.utcnow().isoformat()
                return True

        return instance.is_complete()

    def get_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        """Retrieve workflow instance."""
        return self.instances.get(instance_id)

    def get_status(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow status report."""
        instance = self.instances.get(instance_id)
        if not instance:
            return None

        return {
            "instance_id": instance.instance_id,
            "workflow_name": instance.workflow_name,
            "status": instance.status.value,
            "progress": instance.get_progress(),
            "current_step": instance.current_step,
            "step_statuses": {k: v.value for k, v in instance.step_status.items()},
            "error": instance.error,
            "created_at": instance.created_at,
            "completed_at": instance.completed_at
        }


# Global workflow engine
workflow_engine = WorkflowEngine()


def create_feature_request_workflow() -> WorkflowTemplate:
    """Create template for feature request process."""
    return WorkflowTemplate(
        workflow_id="feature_request",
        name="Feature Request Process",
        description="End-to-end process for implementing a new feature",
        steps=[
            WorkflowStep(
                step_id="intake",
                name="Request Intake",
                owner_department="sales",
                description="Collect and validate feature request",
                requires_approval=False
            ),
            WorkflowStep(
                step_id="design",
                name="Design Specification",
                owner_department="design",
                description="Create detailed design",
                depends_on=["intake"],
                requires_approval=True,
                approval_role="design_lead"
            ),
            WorkflowStep(
                step_id="estimation",
                name="Engineering Estimate",
                owner_department="engineering",
                description="Estimate effort and resources",
                depends_on=["design"]
            ),
            WorkflowStep(
                step_id="budget_approval",
                name="Budget Approval",
                owner_department="finance",
                description="Approve budget allocation",
                depends_on=["estimation"],
                requires_approval=True,
                approval_role="ceo"
            ),
            WorkflowStep(
                step_id="development",
                name="Implementation",
                owner_department="engineering",
                description="Develop the feature",
                depends_on=["budget_approval"]
            ),
            WorkflowStep(
                step_id="qa",
                name="Quality Assurance",
                owner_department="engineering",
                description="Test and validate",
                depends_on=["development"],
                requires_approval=True,
                approval_role="tech_lead"
            ),
            WorkflowStep(
                step_id="launch",
                name="Launch",
                owner_department="product",
                description="Release to production",
                depends_on=["qa"]
            )
        ]
    )


def create_bug_fix_workflow() -> WorkflowTemplate:
    """Create template for bug fix process."""
    return WorkflowTemplate(
        workflow_id="bug_fix",
        name="Bug Fix Workflow",
        description="Fast-track process for critical bugs",
        steps=[
            WorkflowStep(
                step_id="triage",
                name="Bug Triage",
                owner_department="support",
                description="Classify severity and impact",
                requires_approval=False
            ),
            WorkflowStep(
                step_id="fix",
                name="Implement Fix",
                owner_department="engineering",
                description="Code the fix",
                depends_on=["triage"]
            ),
            WorkflowStep(
                step_id="verify",
                name="Verification",
                owner_department="engineering",
                description="Verify fix works",
                depends_on=["fix"],
                requires_approval=True,
                approval_role="tech_lead"
            ),
            WorkflowStep(
                step_id="deploy",
                name="Deploy Fix",
                owner_department="engineering",
                description="Release to production",
                depends_on=["verify"]
            )
        ]
    )
