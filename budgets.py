"""
Budget & Capacity Management - Tracks department spending against allocations
and department staffing capacity against current workload.

This is the resource layer that grounds agent decisions (agent_decisions.py)
and workflow approvals (workflows.py) in finite money and headcount, rather
than letting agents execute or delegate as if resources were unlimited.
"""
import json
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent_state import agent_registry


@dataclass
class ExpenseRecord:
    """A single committed spend against a department budget."""
    department: str
    amount: float
    category: str  # e.g., "labor", "tooling", "vendor"
    description: str = ""
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class DepartmentBudget:
    """Tracks allocation, committed spend, and pending reservations for a department."""
    department: str
    allocated: float
    period: str = "monthly"
    spent: float = 0.0
    # Funds held for in-flight approvals so two concurrent requests can't
    # both pass a check against the same uncommitted dollars.
    reserved: Dict[str, float] = field(default_factory=dict)

    def reserved_total(self) -> float:
        return sum(self.reserved.values())

    def available(self) -> float:
        """Funds that are neither spent nor already reserved."""
        return self.allocated - self.spent - self.reserved_total()

    def utilization_pct(self) -> float:
        if self.allocated <= 0:
            return 0.0
        return (self.spent + self.reserved_total()) / self.allocated

    def can_afford(self, amount: float) -> bool:
        return amount <= self.available()

    def reserve(self, reference_id: str, amount: float) -> bool:
        """Hold funds for a pending approval. Returns False if insufficient."""
        if not self.can_afford(amount):
            return False
        self.reserved[reference_id] = amount
        return True

    def release_reservation(self, reference_id: str):
        """Drop a hold without spending it (e.g., request was rejected)."""
        self.reserved.pop(reference_id, None)

    def commit_reservation(self, reference_id: str) -> float:
        """Convert a hold into actual spend. Returns the committed amount."""
        amount = self.reserved.pop(reference_id, 0.0)
        self.spent += amount
        return amount


class BudgetManager:
    """Central registry and persistence for all department budgets."""

    def __init__(self, data_file: str = "data/budgets.json"):
        self.data_file = Path(data_file)
        self.budgets: Dict[str, DepartmentBudget] = {}
        self.expense_log: List[ExpenseRecord] = []
        self.load()

    def load(self):
        """Load budgets and expense history from file."""
        if not self.data_file.exists():
            return
        with open(self.data_file) as f:
            data = json.load(f)
            for department, budget_data in data.get("budgets", {}).items():
                self.budgets[department] = DepartmentBudget(**budget_data)
            self.expense_log = [ExpenseRecord(**e) for e in data.get("expenses", [])]

    def save(self):
        """Persist budgets and expense history to file."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "budgets": {dept: asdict(b) for dept, b in self.budgets.items()},
            "expenses": [asdict(e) for e in self.expense_log],
        }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

    def allocate(self, department: str, amount: float, period: str = "monthly") -> DepartmentBudget:
        """Set (or reset) a department's budget for a new period."""
        self.budgets[department] = DepartmentBudget(department=department, allocated=amount, period=period)
        self.save()
        return self.budgets[department]

    def get_budget(self, department: str) -> Optional[DepartmentBudget]:
        return self.budgets.get(department)

    def request_expense(self, department: str, amount: float, category: str,
                         description: str = "", task_id: Optional[str] = None,
                         agent_id: Optional[str] = None) -> Tuple[bool, str]:
        """Commit an immediate expense if the department can afford it."""
        budget = self.budgets.get(department)
        if not budget:
            return False, f"No budget allocated for '{department}'"

        if not budget.can_afford(amount):
            return False, (f"Insufficient budget: ${amount:,.2f} requested, "
                            f"${budget.available():,.2f} available")

        budget.spent += amount
        self.expense_log.append(ExpenseRecord(
            department=department, amount=amount, category=category,
            description=description, task_id=task_id, agent_id=agent_id
        ))
        self.save()
        return True, "Expense approved"

    def reserve_funds(self, department: str, reference_id: str, amount: float) -> Tuple[bool, str]:
        """Hold funds for a task pending approval (e.g., a workflow approval gate)."""
        budget = self.budgets.get(department)
        if not budget:
            return False, f"No budget allocated for '{department}'"
        if not budget.reserve(reference_id, amount):
            return False, (f"Insufficient budget to reserve ${amount:,.2f}, "
                            f"${budget.available():,.2f} available")
        self.save()
        return True, "Funds reserved"

    def confirm_reservation(self, department: str, reference_id: str, category: str,
                             description: str = "", task_id: Optional[str] = None,
                             agent_id: Optional[str] = None) -> float:
        """Approval granted: convert a reservation into a logged expense."""
        budget = self.budgets.get(department)
        if not budget:
            return 0.0
        amount = budget.commit_reservation(reference_id)
        if amount > 0:
            self.expense_log.append(ExpenseRecord(
                department=department, amount=amount, category=category,
                description=description, task_id=task_id, agent_id=agent_id
            ))
            self.save()
        return amount

    def cancel_reservation(self, department: str, reference_id: str):
        """Approval denied: release held funds back to the department."""
        budget = self.budgets.get(department)
        if budget:
            budget.release_reservation(reference_id)
            self.save()

    def approve_decision(self, decision, department: str, cost_per_hour: float = 100.0) -> Tuple[bool, str]:
        """Bridge for agent_decisions.DecisionResult: approve only if the
        department can actually afford the estimated cost of the work."""
        if not decision.approval_required:
            return True, "No approval required"

        estimated_cost = (decision.recommended_deadline or 5.0) * cost_per_hour
        approved, reason = self.request_expense(
            department=department, amount=estimated_cost, category="labor",
            description=decision.reasoning, agent_id=decision.assigned_agent_id
        )
        return approved, reason

    def organization_summary(self) -> Dict[str, float]:
        """Organization-wide totals across all department budgets."""
        if not self.budgets:
            return {"total_allocated": 0.0, "total_spent": 0.0, "total_reserved": 0.0, "total_available": 0.0}
        return {
            "total_allocated": sum(b.allocated for b in self.budgets.values()),
            "total_spent": sum(b.spent for b in self.budgets.values()),
            "total_reserved": sum(b.reserved_total() for b in self.budgets.values()),
            "total_available": sum(b.available() for b in self.budgets.values()),
        }

    def over_budget_departments(self, threshold: float = 0.9) -> List[str]:
        """Departments whose utilization has crossed the warning threshold."""
        return [dept for dept, b in self.budgets.items() if b.utilization_pct() >= threshold]


@dataclass
class CapacitySnapshot:
    """Point-in-time staffing capacity for a department."""
    department: str
    agent_count: int
    total_capacity: int  # sum of max_concurrent_tasks across the department's agents
    current_workload: int  # sum of active tasks across those agents
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def utilization_pct(self) -> float:
        if self.total_capacity <= 0:
            return 0.0
        return self.current_workload / self.total_capacity

    def slack(self) -> int:
        """Remaining task slots before the department is fully booked."""
        return max(0, self.total_capacity - self.current_workload)


class CapacityManager:
    """Reads agent_registry to report on and reason about department staffing."""

    def __init__(self, registry=agent_registry):
        self.registry = registry

    def snapshot(self, department: str) -> CapacitySnapshot:
        """Compute current capacity for one department from live agent states."""
        agents = [s for s in self.registry.all().values() if s.profile.department == department]
        return CapacitySnapshot(
            department=department,
            agent_count=len(agents),
            total_capacity=sum(a.profile.max_concurrent_tasks for a in agents),
            current_workload=sum(a.current_workload for a in agents),
        )

    def departments(self) -> List[str]:
        """All departments with at least one registered agent."""
        return sorted({s.profile.department for s in self.registry.all().values()})

    def is_over_capacity(self, department: str, threshold: float = 0.85) -> bool:
        return self.snapshot(department).utilization_pct() >= threshold

    def organization_report(self) -> Dict[str, CapacitySnapshot]:
        """Capacity snapshot for every department currently staffed."""
        return {dept: self.snapshot(dept) for dept in self.departments()}

    def recommend_actions(self, over_threshold: float = 0.85, under_threshold: float = 0.3) -> List[Dict[str, str]]:
        """Flag departments that are stretched thin or sitting mostly idle."""
        recommendations = []
        for dept, snap in self.organization_report().items():
            if snap.agent_count == 0:
                continue
            util = snap.utilization_pct()
            if util >= over_threshold:
                recommendations.append({
                    "type": "hiring_needed",
                    "department": dept,
                    "issue": f"Utilization at {util:.0%} across {snap.agent_count} agents",
                    "action": f"Add headcount or redistribute work out of {dept}",
                })
            elif util <= under_threshold:
                recommendations.append({
                    "type": "underutilized",
                    "department": dept,
                    "issue": f"Utilization at {util:.0%} across {snap.agent_count} agents",
                    "action": f"Route more work to {dept} or reassign idle agents",
                })
        return recommendations

    def organization_utilization(self) -> float:
        """Overall staffing utilization across every department."""
        snapshots = list(self.organization_report().values())
        capacities = [s.total_capacity for s in snapshots if s.total_capacity > 0]
        if not capacities:
            return 0.0
        return statistics.mean(s.utilization_pct() for s in snapshots if s.total_capacity > 0)


# Global instances, mirroring the module-level singletons in agent_state.py and performance.py
budget_manager = BudgetManager()
capacity_manager = CapacityManager()
