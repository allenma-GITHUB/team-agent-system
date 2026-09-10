"""
Workflow Engine - Coordinates multi-step tasks across agents and departments.
Enables complex business processes like feature requests, bug fixes, and reviews.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Tuple
from enum import Enum
from pathlib import Path
import json
import time
from datetime import datetime

from budgets import budget_manager
from departments import DepartmentManager


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
    estimated_cost: float = 0.0  # $ reserved/spent from budget_dept() when this step requires approval
    budget_department: Optional[str] = None  # defaults to owner_department if unset

    def __hash__(self):
        return hash(self.step_id)

    def budget_dept(self) -> str:
        """Department whose budget backs this step (may differ from who executes it,
        e.g. a finance-owned approval step that draws against engineering's budget)."""
        return self.budget_department or self.owner_department


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
    """Orchestrates workflow execution.

    Instances persist to disk (data/workflows.json by default) so state
    survives across separate process runs - e.g. a CLI command that starts
    a workflow and a later one that advances it. Templates are NOT
    persisted: they're supplied by code (create_feature_request_workflow(),
    etc.) and must be re-registered with register_template() at the start
    of every process before operating on a restored instance, the same way
    DepartmentManager re-reads config.json fresh each run instead of
    caching department definitions to disk.
    """

    def __init__(self, data_file: str = "data/workflows.json"):
        self.data_file = Path(data_file)
        self.templates: Dict[str, WorkflowTemplate] = {}
        self.instances: Dict[str, WorkflowInstance] = {}
        self.load()

    def load(self):
        """Load persisted instances from file. Templates are never persisted."""
        if not self.data_file.exists():
            return
        with open(self.data_file) as f:
            data = json.load(f)
        for instance_id, inst_data in data.items():
            self.instances[instance_id] = WorkflowInstance(
                workflow_id=inst_data["workflow_id"],
                workflow_name=inst_data["workflow_name"],
                instance_id=inst_data["instance_id"],
                input_data=inst_data.get("input_data", {}),
                status=WorkflowStatus(inst_data["status"]),
                current_step=inst_data.get("current_step"),
                step_results=inst_data.get("step_results", {}),
                step_status={k: StepStatus(v) for k, v in inst_data.get("step_status", {}).items()},
                created_at=inst_data.get("created_at"),
                started_at=inst_data.get("started_at"),
                completed_at=inst_data.get("completed_at"),
                error=inst_data.get("error"),
            )

    def save(self):
        """Persist instances to file."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for instance_id, instance in self.instances.items():
            data[instance_id] = {
                "workflow_id": instance.workflow_id,
                "workflow_name": instance.workflow_name,
                "instance_id": instance.instance_id,
                "input_data": instance.input_data,
                "status": instance.status.value,
                "current_step": instance.current_step,
                "step_results": instance.step_results,
                "step_status": {k: v.value for k, v in instance.step_status.items()},
                "created_at": instance.created_at,
                "started_at": instance.started_at,
                "completed_at": instance.completed_at,
                "error": instance.error,
            }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

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
        self.save()
        return instance

    def start_instance(self, instance_id: str) -> bool:
        """Start workflow execution."""
        instance = self.instances.get(instance_id)
        if not instance or instance.status != WorkflowStatus.PENDING:
            return False

        instance.status = WorkflowStatus.IN_PROGRESS
        instance.started_at = datetime.utcnow().isoformat()
        self.save()
        return True

    def complete_step(self, instance_id: str, step_id: str, result: Dict[str, Any]) -> bool:
        """Mark a step as complete with results."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        instance.step_results[step_id] = result
        instance.step_status[step_id] = StepStatus.COMPLETED
        self.save()
        return True

    def execute_step(self, instance_id: str, step_id: str, executor) -> bool:
        """Run a step through an executor and mark it complete with the
        real result, instead of requiring a caller to hand-type a
        placeholder result for work nobody actually did.

        `executor` is duck-typed - anything with an
        `execute(department, description) -> dict` method works (e.g.
        task_executor_v2.TaskExecutor). Kept duck-typed rather than
        importing TaskExecutor directly so workflows.py doesn't take on a
        hard dependency on the execution layer just to describe workflows.

        A step requiring approval still needs a separate approve_step()
        call afterward - this only performs and records the work, it
        doesn't grant approval.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        template = self.templates.get(instance.workflow_id)
        step = template.get_step_by_id(step_id) if template else None
        if not step:
            return False

        task_description = f"{step.name}: {step.description}" if step.description else step.name
        result = executor.execute(step.owner_department, task_description)
        return self.complete_step(instance_id, step_id, result)

    @staticmethod
    def _budget_reference(instance_id: str, step_id: str) -> str:
        """Namespaced reservation key so workflow holds don't collide with
        any other subsystem (e.g. task_executor_v2) reserving the same budget."""
        return f"wf:{instance_id}:{step_id}"

    def approve_step(self, instance_id: str, step_id: str, approved: bool) -> bool:
        """Approve/reject a step requiring approval. If the step carries an
        estimated_cost, its budget hold is committed to real spend on approval
        or released back to the department on rejection."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        template = self.templates.get(instance.workflow_id)
        step = template.get_step_by_id(step_id) if template else None
        reference = self._budget_reference(instance_id, step_id)

        if approved:
            instance.step_status[step_id] = StepStatus.APPROVED
            if step and step.estimated_cost > 0:
                budget_manager.confirm_reservation(
                    step.budget_dept(), reference, category="workflow_step",
                    description=step.name, task_id=instance_id
                )
        else:
            instance.step_status[step_id] = StepStatus.REJECTED
            instance.status = WorkflowStatus.PAUSED
            instance.error = f"Step {step_id} rejected"
            if step and step.estimated_cost > 0:
                budget_manager.cancel_reservation(step.budget_dept(), reference)

        self.save()
        return True

    def retry_blocked_step(self, instance_id: str) -> Tuple[bool, str]:
        """Re-attempt a BLOCKED step's budget reservation - e.g. after the
        department's budget has been topped up for a new period. A step
        stays BLOCKED/the workflow stays ESCALATED forever otherwise; this
        is the only way out short of abandoning the instance."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False, "Instance not found"

        step_id = instance.current_step
        if not step_id or instance.step_status.get(step_id) != StepStatus.BLOCKED:
            return False, "No blocked step to retry"

        template = self.templates.get(instance.workflow_id)
        step = template.get_step_by_id(step_id) if template else None
        if not step:
            return False, "Step definition not found"

        department = step.budget_dept()
        reference = self._budget_reference(instance_id, step_id)
        reserved, reason = budget_manager.reserve_funds(department, reference, step.estimated_cost)
        if not reserved:
            instance.error = f"Step '{step.name}' still blocked: {reason}"
            self.save()
            return False, reason

        instance.step_status[step_id] = StepStatus.IN_PROGRESS
        instance.status = WorkflowStatus.IN_PROGRESS
        instance.error = None
        self.save()
        return True, "Resumed"

    def get_next_step(self, instance_id: str) -> Optional[WorkflowStep]:
        """Get next step to execute. A step requiring approval with a non-zero
        estimated_cost must first reserve that amount from its department's
        budget; if the department can't afford it, the step is blocked and the
        workflow escalates instead of silently proceeding into an approval gate
        no one can actually fund."""
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
        if not next_step:
            return None

        if next_step.requires_approval and next_step.estimated_cost > 0:
            department = next_step.budget_dept()
            budget_manager.ensure_allocated(department, DepartmentManager.get_monthly_budget(department))
            reference = self._budget_reference(instance_id, next_step.step_id)
            reserved, reason = budget_manager.reserve_funds(department, reference, next_step.estimated_cost)
            if not reserved:
                instance.current_step = next_step.step_id
                instance.step_status[next_step.step_id] = StepStatus.BLOCKED
                instance.status = WorkflowStatus.ESCALATED
                instance.error = f"Step '{next_step.name}' blocked: {reason}"
                self.save()
                return None

        instance.current_step = next_step.step_id
        instance.step_status[next_step.step_id] = StepStatus.IN_PROGRESS
        self.save()

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
                self.save()
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
                approval_role="design_lead",
                estimated_cost=3000
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
                approval_role="ceo",
                estimated_cost=15000,
                budget_department="engineering"  # finance approves it, engineering's budget funds it
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
                approval_role="tech_lead",
                estimated_cost=500  # fast-track bug fix: lighter cost than a full feature's QA step
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
