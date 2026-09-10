"""
Strategic Planning - CEO-level, organization-wide budget reallocation.

Phase 3's BudgetManager/CapacityManager already compute exactly which
departments are underspending and which are approaching their limit
(BudgetManager.over_budget_departments(), DepartmentBudget.utilization_pct()).
Nothing ever acted on that signal - a department stuck at 95% utilization
stayed there even while another sat at 5%. This turns that signal into a
concrete, reviewable reallocation: move unspent budget from an underspending
department to one running out of room, without ever draining a donor below
a protected reserve.
"""
from dataclasses import dataclass
from typing import List

from budgets import BudgetManager, CapacityManager
from budgets import budget_manager as _default_budget_manager, capacity_manager as _default_capacity_manager


@dataclass
class ReallocationProposal:
    """A single proposed budget transfer between two departments."""
    from_department: str
    to_department: str
    amount: float
    reason: str


class StrategicPlanner:
    """CEO-level reallocation of budget across departments based on
    real utilization signals, not capacity (there's no "hire more staff
    with money" mechanic in this system, so capacity utilization isn't
    the right signal for a *budget* move - budget utilization is)."""

    def __init__(self, budgets: BudgetManager = None, capacity: CapacityManager = None,
                 reserve_pct: float = 0.2):
        self.budgets = budgets or _default_budget_manager
        self.capacity = capacity or _default_capacity_manager
        # Never propose draining a donor below this fraction of its own
        # allocation - "underspending so far" isn't "safe to zero out."
        self.reserve_pct = reserve_pct

    def propose_reallocations(self, low_threshold: float = 0.3,
                               high_threshold: float = 0.85) -> List[ReallocationProposal]:
        """Propose moving unspent budget from departments underspending
        (utilization <= low_threshold) to departments approaching or over
        their limit (utilization >= high_threshold). Greedy: largest donors
        fund largest shortfalls first. Returns an empty list if nothing
        crosses either threshold - most days, there's nothing to propose."""
        donors = []
        recipients = []

        for dept, budget in self.budgets.budgets.items():
            util = budget.utilization_pct()
            if util <= low_threshold:
                giveable = budget.available() - (budget.allocated * self.reserve_pct)
                if giveable > 0:
                    donors.append([dept, giveable, util])
            elif util >= high_threshold:
                committed = budget.spent + budget.reserved_total()
                needed = committed / high_threshold - budget.allocated
                if needed > 0:
                    recipients.append([dept, needed, util])

        donors.sort(key=lambda d: -d[1])
        recipients.sort(key=lambda r: -r[1])

        proposals = []
        donor_idx = 0
        for dept_to, needed, to_util in recipients:
            remaining = needed
            while remaining > 0.01 and donor_idx < len(donors):
                dept_from, available, from_util = donors[donor_idx]
                amount = min(available, remaining)
                if amount > 0.01:
                    proposals.append(ReallocationProposal(
                        from_department=dept_from, to_department=dept_to, amount=round(amount, 2),
                        reason=(f"{dept_from} at {from_util:.0%} utilization (underspending) -> "
                                f"{dept_to} at {to_util:.0%} utilization (approaching limit)")
                    ))
                    remaining -= amount
                    donors[donor_idx][1] -= amount
                if donors[donor_idx][1] <= 0.01:
                    donor_idx += 1

        return proposals

    def apply_reallocations(self, proposals: List[ReallocationProposal]) -> None:
        """Execute a set of proposals: move `allocated` between departments
        and persist. Does not re-validate against current thresholds - call
        propose_reallocations() again first if state may have changed."""
        for proposal in proposals:
            from_budget = self.budgets.get_budget(proposal.from_department)
            to_budget = self.budgets.get_budget(proposal.to_department)
            if not from_budget or not to_budget:
                continue
            from_budget.allocated -= proposal.amount
            to_budget.allocated += proposal.amount
        self.budgets.save()

    def rebalance(self, low_threshold: float = 0.3, high_threshold: float = 0.85) -> List[ReallocationProposal]:
        """Propose and immediately apply. Convenience for a caller that
        trusts the heuristic outright rather than reviewing proposals first
        (e.g. an automated periodic job, vs. a human-reviewed CLI command)."""
        proposals = self.propose_reallocations(low_threshold, high_threshold)
        self.apply_reallocations(proposals)
        return proposals


# Global instance, mirroring the module-level singletons in budgets.py/performance.py
strategic_planner = StrategicPlanner()
