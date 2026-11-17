#!/usr/bin/env python3
"""
Checkpoint 100. BudgetManager.approve_decision() computed the labor charge
for an approval-required decision as `(decision.recommended_deadline or 5.0)
* cost_per_hour`. `recommended_deadline` is set verbatim from the real
`estimated_hours` a caller gave the task (agent_decisions.py's decide(),
both `execute` returns) - never a placeholder - and `submit_task()`/the
CLI's `--hours` validation explicitly allow 0.0 (only negative values are
rejected). But Python's `or` treats `0.0` as falsy, so a task legitimately
estimated at exactly 0 hours that also requires approval was silently
billed as a 5-hour task instead of a 0-hour one - a fabricated charge for
real department money, on the only call path that consumes
`recommended_deadline` arithmetically in production
(task_executor_v2.py's `_run_locked()` -> budgets.py's `approve_decision()`).

Confirmed live before the fix, via a real DepartmentHeadAgent
(finance, isolated registry/budget/capacity/analytics, no LLM provider)
running a task estimated at 0.0 hours and complex enough to require
approval: the department was charged $1,050 (5h x $210/h) for a task
declared to need 0 hours.

Fixed: `approve_decision()` now only falls back to the 5.0h default when
`recommended_deadline` is genuinely absent (`None` - e.g. a decision built
without ever setting it), not merely falsy. `decision.recommended_deadline
is not None` replaces the `or` check. No change to the 5.0h default value
itself, `request_expense()`'s unrelated direct-charge path (already
correct - it multiplies `estimated_hours` with no `or` fallback), or
anything in agent_decisions.py/task_executor_v2.py.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_decisions import DecisionResult
from budgets import BudgetManager


TEST_BUDGETS_FILE = "data/test_budget_zero_hour_approval.json"


def _fresh_manager() -> BudgetManager:
    if os.path.exists(TEST_BUDGETS_FILE):
        os.remove(TEST_BUDGETS_FILE)
    mgr = BudgetManager(data_file=TEST_BUDGETS_FILE)
    mgr.allocate("finance", 20000.0)
    return mgr


def _decision(recommended_deadline):
    return DecisionResult(
        decision="execute",
        assigned_agent_id="finance_head",
        reasoning="test decision",
        confidence=0.9,
        approval_required=True,
        recommended_deadline=recommended_deadline,
    )


def test_zero_hour_task_billed_zero():
    """The exact live false positive: a 0.0h task must cost $0, not $500."""
    mgr = _fresh_manager()
    approved, reason = mgr.approve_decision(
        _decision(0.0), department="finance", cost_per_hour=210.0, task_id="t1"
    )
    assert approved, reason
    budget = mgr.get_budget("finance")
    assert budget.spent == 0.0, f"expected $0 charge for a 0-hour task, got ${budget.spent}"


def test_missing_estimate_still_falls_back_to_default():
    """A genuinely-unset estimate (None) must still use the 5.0h default -
    this fix narrows the falsy check, it doesn't remove the fallback."""
    mgr = _fresh_manager()
    approved, reason = mgr.approve_decision(
        _decision(None), department="finance", cost_per_hour=210.0, task_id="t2"
    )
    assert approved, reason
    budget = mgr.get_budget("finance")
    assert budget.spent == 5.0 * 210.0, (
        f"expected the {5.0 * 210.0} default-fallback charge for a None estimate, got {budget.spent}"
    )


def test_normal_nonzero_estimate_unaffected():
    """A real, truthy hour estimate must pass through exactly as before."""
    mgr = _fresh_manager()
    approved, reason = mgr.approve_decision(
        _decision(3.0), department="finance", cost_per_hour=210.0, task_id="t3"
    )
    assert approved, reason
    budget = mgr.get_budget("finance")
    assert budget.spent == 3.0 * 210.0, f"expected ${3.0 * 210.0} charge for a 3-hour task, got ${budget.spent}"


if __name__ == "__main__":
    test_zero_hour_task_billed_zero()
    print("PASS: 0-hour approval-required task is billed $0, not the 5h fallback")
    test_missing_estimate_still_falls_back_to_default()
    print("PASS: a genuinely-unset estimate (None) still falls back to the 5.0h default")
    test_normal_nonzero_estimate_unaffected()
    print("PASS: a normal nonzero hour estimate is unaffected")

    if os.path.exists(TEST_BUDGETS_FILE):
        os.remove(TEST_BUDGETS_FILE)
    print("\nAll tests passed.")
