#!/usr/bin/env python3
"""
Checkpoint 101. StrategicPlanner.propose_reallocations() scored each
department's utilization by reading self.budgets.budgets.items() directly -
the raw DepartmentBudget objects, bypassing BudgetManager.get_budget().
Every other BudgetManager-wide read in this codebase (organization_summary(),
over_budget_departments(), rollover_history()) calls _roll_if_due()/
_roll_all_due() first, so a department whose period has already elapsed is
reset into its new, empty period (spent -> 0.0) before being scored. This
method was the one read of self.budgets.budgets that skipped that check.

Confirmed live before the fix: a department allocated $1,000 on a "daily"
period, with $950 (95%) spent and period_start backdated two days (so
period_elapsed() is True - a real rollover is due but nothing has triggered
it yet), was still read as 95%-utilized by propose_reallocations() and
proposed - from a real underspending donor - a $117.65 reallocation to
cover a shortfall that a rollover would have already erased (spent resets
to 0.0, utilization to 0%). Had apply_reallocations()/rebalance() then run
on that proposal (the CLI's `strategy --apply`, or rebalance()'s own
propose-and-apply-in-one-call convenience), the donor's `allocated` would
have been permanently reduced to fund a need that no longer existed by the
time the transfer happened.

Fixed: propose_reallocations() now reads each department through
get_budget() instead of self.budgets.budgets.items() directly - the same
"every real access path routes through the check" rule rollover_history()'s
own docstring already states for this file's other organization-wide
reads. No change to the threshold logic, the greedy donor/recipient
matching, the reserve_pct protection, apply_reallocations(), or rebalance()
itself.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from budgets import BudgetManager
from strategy import StrategicPlanner


def _elapsed_daily_budget(budgets: BudgetManager, department: str, allocated: float,
                          spent: float, days_ago: float = 2.0):
    """Allocate `department` on a daily period, spend `spent`, and backdate
    period_start far enough that period_elapsed() is True - a real rollover
    is due but nothing has triggered it yet, matching the repro above."""
    budgets.allocate(department, allocated, period="daily")
    budget = budgets.budgets[department]
    budget.spent = spent
    budget.period_start = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    budgets.save()
    return budget


def test_elapsed_period_is_not_scored_as_still_over_threshold():
    """The exact live false positive proven above: a department whose daily
    period has already elapsed, sitting at 95% spend from that old period,
    must not be proposed as a recipient - a rollover would reset it to 0%
    before any real transfer could happen."""
    budgets = BudgetManager(data_file="data/test_strategy_rollover_budgets.json")
    _elapsed_daily_budget(budgets, "qa_rollover_recipient", 1000.0, 950.0)
    _elapsed_daily_budget(budgets, "qa_rollover_donor", 1000.0, 50.0)

    planner = StrategicPlanner(budgets=budgets)
    proposals = planner.propose_reallocations()

    assert proposals == [], f"expected no proposals once elapsed periods roll, got {proposals}"


def test_propose_reallocations_itself_triggers_the_rollover():
    """Reading through get_budget() doesn't just avoid scoring stale data -
    it actually rolls the elapsed period, the same side effect every other
    organization-wide BudgetManager read already has. Both departments'
    `spent` must be reset to 0.0 by the call, not left stale for some later
    caller to roll instead."""
    budgets = BudgetManager(data_file="data/test_strategy_rollover_budgets2.json")
    _elapsed_daily_budget(budgets, "qa_rollover_a", 1000.0, 950.0)
    _elapsed_daily_budget(budgets, "qa_rollover_b", 1000.0, 50.0)

    planner = StrategicPlanner(budgets=budgets)
    planner.propose_reallocations()

    assert budgets.budgets["qa_rollover_a"].spent == 0.0
    assert budgets.budgets["qa_rollover_b"].spent == 0.0
    # And it's a real, logged rollover - not just a silently zeroed field.
    rolled_departments = {r.department for r in budgets.rollover_log}
    assert "qa_rollover_a" in rolled_departments
    assert "qa_rollover_b" in rolled_departments


def test_current_period_overspend_is_still_proposed_normally():
    """Regression guard: a department genuinely over threshold IN ITS
    CURRENT, not-yet-elapsed period must still be proposed exactly as
    before this fix - the fix narrows the stale-data case, it doesn't make
    propose_reallocations() stop firing for a real, live shortfall."""
    budgets = BudgetManager(data_file="data/test_strategy_rollover_budgets3.json")
    budgets.allocate("qa_rollover_live_donor", 10000)
    budgets.request_expense("qa_rollover_live_donor", 500, category="labor")  # 5%
    budgets.allocate("qa_rollover_live_recipient", 10000)
    budgets.request_expense("qa_rollover_live_recipient", 9500, category="labor")  # 95%

    planner = StrategicPlanner(budgets=budgets)
    proposals = planner.propose_reallocations(low_threshold=0.3, high_threshold=0.85)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.from_department == "qa_rollover_live_donor"
    assert proposal.to_department == "qa_rollover_live_recipient"
    # recipient needs: 9500 / 0.85 - 10000 = 11176.47 - 10000 = 1176.47
    assert abs(proposal.amount - 1176.47) < 1.0


def main():
    test_elapsed_period_is_not_scored_as_still_over_threshold()
    print("[OK] elapsed period is not scored as still over threshold")
    test_propose_reallocations_itself_triggers_the_rollover()
    print("[OK] propose_reallocations() itself triggers the due rollover")
    test_current_period_overspend_is_still_proposed_normally()
    print("[OK] current-period overspend is still proposed normally")
    print("\n[OK] All strategy-rollover tests passed!")

    import pathlib
    for f in ["data/test_strategy_rollover_budgets.json",
              "data/test_strategy_rollover_budgets2.json",
              "data/test_strategy_rollover_budgets3.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
