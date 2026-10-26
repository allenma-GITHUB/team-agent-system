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


# Fixed day-counts rather than real calendar months/quarters/years - this
# project is standard-library only (CLAUDE.md's "zero third-party
# dependencies"), and real calendar math (a "monthly" period ending on the
# same day-of-month it started) needs one. An approximation, documented
# here rather than hidden: "monthly" rolls every 30 days, not on each
# month's actual day count. A period name not in this map never elapses
# (see DepartmentBudget.period_elapsed) rather than guessing a length.
PERIOD_LENGTHS_DAYS: Dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 90,
    "annual": 365,
}


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
    # When the current period began. Needed to tell whether `period` has
    # actually elapsed - before this field existed, `period` was recorded
    # and read back on every load but had nothing to measure elapsed time
    # against, so it could never trigger anything (see BudgetManager's
    # module docstring note and CHECKPOINT 76's "found, not fixed" entry).
    period_start: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    # Funds held for in-flight approvals so two concurrent requests can't
    # both pass a check against the same uncommitted dollars.
    reserved: Dict[str, float] = field(default_factory=dict)

    def period_elapsed(self, now: Optional[datetime] = None) -> bool:
        """True once this budget's period has fully run its length.

        An unrecognized `period` name (not in PERIOD_LENGTHS_DAYS) never
        elapses - same absence-means-never-computed convention the rest of
        this module follows - rather than guessing a length for it.
        """
        length_days = PERIOD_LENGTHS_DAYS.get(self.period)
        if length_days is None:
            return False
        now = now or datetime.utcnow()
        started = datetime.fromisoformat(self.period_start)
        return (now - started).days >= length_days

    def days_until_rollover(self, now: Optional[datetime] = None) -> Optional[float]:
        """Days left in the current period, or None for an unrecognized
        period name (see period_elapsed)."""
        length_days = PERIOD_LENGTHS_DAYS.get(self.period)
        if length_days is None:
            return None
        now = now or datetime.utcnow()
        started = datetime.fromisoformat(self.period_start)
        elapsed_days = (now - started).total_seconds() / 86400
        return max(0.0, length_days - elapsed_days)

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


@dataclass
class PeriodRollover:
    """Record of one department's budget period resetting. A rollover
    zeroes `spent` so the next period starts clean - recorded here rather
    than done silently, so an operator reading expense_log/rollover
    history can still see what the old period actually spent and that
    unspent allocation does not carry forward (a "use it or lose it"
    budget, not an accumulating one)."""
    department: str
    period: str
    period_start: str  # the period that just ended
    rolled_at: str
    spent_in_period: float
    unused_forfeited: float


class BudgetManager:
    """Central registry and persistence for all department budgets."""

    def __init__(self, data_file: str = "data/budgets.json"):
        self.data_file = Path(data_file)
        self.budgets: Dict[str, DepartmentBudget] = {}
        self.expense_log: List[ExpenseRecord] = []
        self.rollover_log: List[PeriodRollover] = []
        self.load()

    def load(self):
        """Load budgets, expense history, and rollover history from file."""
        if not self.data_file.exists():
            return
        with open(self.data_file) as f:
            data = json.load(f)
            for department, budget_data in data.get("budgets", {}).items():
                self.budgets[department] = DepartmentBudget(**budget_data)
            self.expense_log = [ExpenseRecord(**e) for e in data.get("expenses", [])]
            self.rollover_log = [PeriodRollover(**r) for r in data.get("rollovers", [])]

    def save(self):
        """Persist budgets, expense history, and rollover history to file."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "budgets": {dept: asdict(b) for dept, b in self.budgets.items()},
            "expenses": [asdict(e) for e in self.expense_log],
            "rollovers": [asdict(r) for r in self.rollover_log],
        }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _roll_if_due(self, budget: DepartmentBudget) -> Optional[PeriodRollover]:
        """Lazily resets a budget into a fresh period once its period has
        elapsed, logging what happened instead of just zeroing `spent`.

        Checked on every real read/write path (get_budget, and everything
        below that routes through it) since this project runs no
        background scheduler - there is nothing else that would ever
        notice a period boundary passing between runs.

        `reserved` funds are deliberately left untouched: they represent
        in-flight approvals a caller still expects to find by
        reference_id (confirm_reservation/cancel_reservation); clearing
        them here would silently orphan a pending approval mid-flight.
        Only committed `spent` resets at a period boundary. If more than
        one full period elapsed while nothing touched this budget (e.g.
        the system was down), this still only ever rolls once, into a
        period that starts now - it does not try to replay each missed
        period in turn.
        """
        if not budget.period_elapsed():
            return None
        record = PeriodRollover(
            department=budget.department,
            period=budget.period,
            period_start=budget.period_start,
            rolled_at=datetime.utcnow().isoformat(),
            spent_in_period=budget.spent,
            unused_forfeited=budget.available(),
        )
        budget.spent = 0.0
        budget.period_start = record.rolled_at
        self.rollover_log.append(record)
        self.save()
        return record

    def _roll_all_due(self):
        """Apply _roll_if_due to every tracked department, for callers
        (organization_summary, over_budget_departments) that read the
        whole self.budgets dict directly instead of one get_budget() at
        a time."""
        for budget in self.budgets.values():
            self._roll_if_due(budget)

    def allocate(self, department: str, amount: float, period: str = "monthly") -> DepartmentBudget:
        """Set (or reset) a department's budget for a new period."""
        self.budgets[department] = DepartmentBudget(department=department, allocated=amount, period=period)
        self.save()
        return self.budgets[department]

    def ensure_allocated(self, department: str, amount: float, period: str = "monthly") -> DepartmentBudget:
        """Allocate a starting budget only if the department doesn't have one yet.

        Lets callers seed a default budget on first use (e.g. from config.json)
        without resetting spend that's already been tracked for the period.
        """
        if department not in self.budgets:
            return self.allocate(department, amount, period)
        return self.budgets[department]

    def get_budget(self, department: str) -> Optional[DepartmentBudget]:
        budget = self.budgets.get(department)
        if budget is not None:
            self._roll_if_due(budget)
        return budget

    def request_expense(self, department: str, amount: float, category: str,
                         description: str = "", task_id: Optional[str] = None,
                         agent_id: Optional[str] = None) -> Tuple[bool, str]:
        """Commit an immediate expense if the department can afford it."""
        if amount < 0:
            # A negative "expense" would decrease spent instead of increasing
            # it, manufacturing available budget rather than spending it.
            return False, f"Expense amount cannot be negative: ${amount:,.2f}"

        budget = self.get_budget(department)
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
        if amount < 0:
            return False, f"Reservation amount cannot be negative: ${amount:,.2f}"

        budget = self.get_budget(department)
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
        budget = self.get_budget(department)
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
        budget = self.get_budget(department)
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
        self._roll_all_due()
        if not self.budgets:
            return {"total_allocated": 0.0, "total_spent": 0.0, "total_reserved": 0.0, "total_available": 0.0}
        return {
            "total_allocated": sum(b.allocated for b in self.budgets.values()),
            "total_spent": sum(b.spent for b in self.budgets.values()),
            "total_reserved": sum(b.reserved_total() for b in self.budgets.values()),
            "total_available": sum(b.available() for b in self.budgets.values()),
        }

    def expense_breakdown(self, department: Optional[str] = None) -> Dict[str, float]:
        """Total committed spend per expense category ("labor", "llm_tokens",
        "workflow_step", ...), org-wide or scoped to one department.

        Every real spend in this system - the labor charge in
        decide_on_task()'s budget check, the token charge after an LLM run,
        a workflow step's committed reservation - has logged an
        ExpenseRecord with a real `category` since the budget system
        existed, and every one of those records has been persisted to
        data/budgets.json and reloaded on every startup. Nothing ever
        summed them back up: organization_summary() and DepartmentBudget
        itself only ever answered "how much", never "on what" - the same
        shape as this project's other "computed and recorded, zero
        consumer" gaps. Returns {} rather than a department's nonexistent
        key when no expense has been logged yet, matching this file's own
        absence-means-never-spent convention.
        """
        breakdown: Dict[str, float] = {}
        for expense in self.expense_log:
            if department is not None and expense.department != department:
                continue
            breakdown[expense.category] = breakdown.get(expense.category, 0.0) + expense.amount
        return breakdown

    def over_budget_departments(self, threshold: float = 0.9) -> List[str]:
        """Departments whose utilization has crossed the warning threshold."""
        self._roll_all_due()
        return [dept for dept, b in self.budgets.items() if b.utilization_pct() >= threshold]

    def rollover_history(self, department: Optional[str] = None) -> List[PeriodRollover]:
        """Every recorded period rollover, newest first - org-wide or scoped
        to one department. `rollover_log` has logged a real PeriodRollover
        every time `_roll_if_due()` actually fires since the rollover fix
        shipped, and persists it to data/budgets.json's "rollovers" array,
        but nothing has ever read it back: `_roll_if_due()` logs rollovers,
        `_roll_all_due()` triggers them on the organization-wide reads, and
        both leave the result sitting in memory and on disk with no CLI
        command or API route to see it - the one gap that fix's own session
        named and deliberately left for a session of its own. Calls
        `_roll_all_due()` first so a rollover that just became due is
        reflected here immediately, the same "every real access path routes
        through the check" rule `get_budget()`/`organization_summary()`
        already follow, rather than only showing up the next time something
        else happens to touch that department's budget."""
        self._roll_all_due()
        matches = [r for r in self.rollover_log if department is None or r.department == department]
        return sorted(matches, key=lambda r: r.rolled_at, reverse=True)


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

    def earliest_relief(self, department: str) -> Optional[str]:
        """Earliest availability_until among the department's agents that
        actually have one set (i.e. carry an active backlog right now), or
        None if nobody in the department does - either because everyone is
        idle, or because availability_until is a live projection (see
        AgentState's own field comment) and nothing has moved it yet.
        Deliberately omitted rather than guessed when unknown, same
        absence-means-never-computed convention the rest of this project's
        reporting already follows."""
        timestamps = [
            s.availability_until
            for s in self.registry.all().values()
            if s.profile.department == department and s.availability_until
        ]
        return min(timestamps) if timestamps else None

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
            # is_over_capacity() already encodes this exact threshold check;
            # before this fix it had no caller anywhere (including tests) and
            # this method re-derived the comparison by hand instead of using it.
            if self.is_over_capacity(dept, over_threshold):
                recommendation = {
                    "type": "hiring_needed",
                    "department": dept,
                    "issue": f"Utilization at {util:.0%} across {snap.agent_count} agents",
                    "action": f"Add headcount or redistribute work out of {dept}",
                }
                # Omitted, not guessed, when no agent in the department has
                # an active backlog recorded yet - see earliest_relief()'s
                # own comment on why this is a projection, not a real ETA.
                relief = self.earliest_relief(dept)
                if relief:
                    recommendation["earliest_relief_at"] = relief
                recommendations.append(recommendation)
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
