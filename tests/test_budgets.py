#!/usr/bin/env python3
"""
Test budget tracking and capacity management.
Demonstrates department budgets, expense approval, reservation flows,
and staffing capacity reports built on top of the existing agent registry.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timedelta

from agent_state import AgentProfile, AgentRegistry
from agent_decisions import DecisionResult
from budgets import BudgetManager, CapacityManager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_budget_allocation_and_spend():
    """Basic allocation, spending, and insufficient-funds handling."""
    print_section("1. Budget Allocation & Spending")

    budgets = BudgetManager(data_file="data/test_budgets.json")
    budgets.allocate("engineering", 10000, period="monthly")

    approved, reason = budgets.request_expense(
        "engineering", 2000, category="labor", description="Sprint work"
    )
    print(f"  Request $2,000: approved={approved} ({reason})")
    assert approved

    budget = budgets.get_budget("engineering")
    print(f"  Spent: ${budget.spent:,.2f} / Allocated: ${budget.allocated:,.2f}")
    print(f"  Utilization: {budget.utilization_pct():.0%}")
    assert budget.spent == 2000

    # Try to overspend
    approved, reason = budgets.request_expense("engineering", 9000, category="labor")
    print(f"  Request $9,000 (over remaining): approved={approved} ({reason})")
    assert not approved


def test_reservation_flow():
    """Reserve funds for a pending approval, then confirm or cancel."""
    print_section("2. Reservation Flow (Pending Approvals)")

    budgets = BudgetManager(data_file="data/test_budgets.json")
    budgets.allocate("design", 5000)

    ok, reason = budgets.reserve_funds("design", "wf_step_7", 1500)
    print(f"  Reserve $1,500 for workflow step: {ok} ({reason})")
    assert ok

    budget = budgets.get_budget("design")
    print(f"  Available after reservation: ${budget.available():,.2f}")
    assert budget.available() == 3500

    # A second request that would exceed what's left after the reservation
    ok, reason = budgets.reserve_funds("design", "wf_step_8", 4000)
    print(f"  Reserve $4,000 (exceeds remaining): {ok} ({reason})")
    assert not ok

    # Approval granted: convert the first reservation into a real expense
    committed = budgets.confirm_reservation("design", "wf_step_7", category="vendor")
    print(f"  Confirmed reservation -> ${committed:,.2f} committed to spend")
    assert budget.spent == 1500 and budget.reserved_total() == 0


def test_org_summary_and_alerts():
    """Organization-wide rollups and over-budget alerts."""
    print_section("3. Organization Summary & Alerts")

    budgets = BudgetManager(data_file="data/test_budgets.json")
    budgets.allocate("support", 1000)
    budgets.request_expense("support", 950, category="labor")

    summary = budgets.organization_summary()
    print(f"  Total allocated: ${summary['total_allocated']:,.2f}")
    print(f"  Total spent: ${summary['total_spent']:,.2f}")
    print(f"  Total available: ${summary['total_available']:,.2f}")

    over_budget = budgets.over_budget_departments(threshold=0.9)
    print(f"  Departments over 90% utilization: {over_budget}")
    assert "support" in over_budget


def test_decision_bridge():
    """A DecisionResult that needs approval should draw from the department budget."""
    print_section("4. Bridging Agent Decisions to Budget Approval")

    budgets = BudgetManager(data_file="data/test_budgets.json")
    budgets.allocate("engineering", 100000)

    decision = DecisionResult(
        decision="execute",
        assigned_agent_id="eng_lead",
        reasoning="Major architecture redesign",
        confidence=0.7,
        approval_required=True,
        recommended_deadline=40  # hours
    )

    approved, reason = budgets.approve_decision(decision, department="engineering", cost_per_hour=100)
    print(f"  40-hour task ($100/hr) approval: {approved} ({reason})")
    assert approved

    budget = budgets.get_budget("engineering")
    print(f"  Spent after approval: ${budget.spent:,.2f}")
    assert budget.spent == 4000


def test_capacity_management():
    """Capacity snapshots and recommendations built from live agent state."""
    print_section("5. Capacity Management")

    # Isolated registry so this demo doesn't pollute the shared agent roster.
    test_registry = AgentRegistry(data_file="data/test_agent_states.json")

    # Register a small department with one overloaded and one idle agent.
    test_registry.register(AgentProfile(
        agent_id="cap_test_busy", name="Busy Agent", agent_type="ManagerAgent",
        department="capacity_test", expertise_areas=["ops"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=2
    ))
    test_registry.register(AgentProfile(
        agent_id="cap_test_idle", name="Idle Agent", agent_type="SpecialistAgent",
        department="capacity_test", expertise_areas=["ops"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=4
    ))
    test_registry.get("cap_test_busy").current_workload = 2  # fully booked

    capacity = CapacityManager(registry=test_registry)
    snapshot = capacity.snapshot("capacity_test")
    print(f"  Department: {snapshot.department}")
    print(f"  Agents: {snapshot.agent_count}, Capacity: {snapshot.total_capacity}, "
          f"Workload: {snapshot.current_workload}")
    print(f"  Utilization: {snapshot.utilization_pct():.0%}, Slack: {snapshot.slack()} tasks")

    assert snapshot.agent_count == 2
    assert snapshot.total_capacity == 6
    assert snapshot.current_workload == 2

    recs = capacity.recommend_actions(over_threshold=0.9, under_threshold=0.9)
    print(f"  Recommendations at 90% thresholds: {len(recs)}")
    for rec in recs:
        print(f"    - [{rec['type']}] {rec['department']}: {rec['action']}")


def test_period_rollover_resets_spend_but_not_reservations():
    """`period`/`period_start` existed since the first commit with nothing
    ever reading them back - a monthly budget stayed fully spent forever,
    no matter how much real time passed (see DAILY_PROGRESS.md CHECKPOINT
    76's "found, not fixed" entry). Confirms the lazy rollover actually
    fires once the period has elapsed, resets spend, leaves an in-flight
    reservation alone, and leaves the allocation itself untouched."""
    print_section("6. Period Rollover Resets Spend, Not Reservations")

    budgets = BudgetManager(data_file="data/test_budgets_rollover.json")
    budget = budgets.allocate("marketing", 10000, period="monthly")
    budgets.request_expense("marketing", 4000, category="labor")
    budgets.reserve_funds("marketing", "wf_step_rollover", 1000)
    print(f"  Before: spent=${budget.spent:,.2f} reserved=${budget.reserved_total():,.2f} "
          f"days_until_rollover={budget.days_until_rollover():.1f}")
    assert not budget.period_elapsed()

    # Backdate the period's start past its 30-day length, simulating real
    # time having passed between runs (there is no clock to fast-forward).
    budget.period_start = (datetime.utcnow() - timedelta(days=31)).isoformat()
    assert budget.period_elapsed()

    rolled = budgets.get_budget("marketing")
    print(f"  After rollover: spent=${rolled.spent:,.2f} reserved=${rolled.reserved_total():,.2f} "
          f"allocated=${rolled.allocated:,.2f}")
    assert rolled.spent == 0.0
    assert rolled.reserved_total() == 1000  # in-flight approval must survive
    assert rolled.allocated == 10000  # same recurring allocation, not reset to 0
    assert not rolled.period_elapsed()  # period_start just advanced to now

    assert len(budgets.rollover_log) == 1
    record = budgets.rollover_log[0]
    print(f"  Logged: spent_in_period=${record.spent_in_period:,.2f} "
          f"unused_forfeited=${record.unused_forfeited:,.2f}")
    assert record.department == "marketing"
    assert record.spent_in_period == 4000
    assert record.unused_forfeited == 5000  # 10000 - 4000 spent - 1000 reserved

    # A pending approval from before the rollover must still resolve against
    # the same reservation - the rollover must not have orphaned it.
    committed = budgets.confirm_reservation("marketing", "wf_step_rollover", category="vendor")
    print(f"  Post-rollover reservation still resolves: committed=${committed:,.2f}")
    assert committed == 1000
    assert rolled.spent == 1000  # resets to the new period's own spend, then this


def test_period_rollover_persists_across_reload():
    """The rollover must survive a process restart, not just an in-memory
    mutation - a fresh BudgetManager reading the same file should see it
    applied exactly once, not re-applied on every subsequent load."""
    print_section("7. Period Rollover Persists To Disk, Applies Once")

    data_file = "data/test_budgets_reload.json"
    budgets = BudgetManager(data_file=data_file)
    budgets.allocate("ops", 5000, period="weekly")
    budgets.request_expense("ops", 4500, category="labor")

    # Backdate directly in the persisted file - the same shape a real
    # multi-day gap between runs would leave on disk.
    data = json.loads(open(data_file).read())
    data["budgets"]["ops"]["period_start"] = (datetime.utcnow() - timedelta(days=8)).isoformat()
    with open(data_file, "w") as f:
        json.dump(data, f)

    reloaded = BudgetManager(data_file=data_file)
    budget = reloaded.get_budget("ops")  # triggers the lazy rollover
    print(f"  First reload: spent=${budget.spent:,.2f}, rollovers logged={len(reloaded.rollover_log)}")
    assert budget.spent == 0.0
    assert len(reloaded.rollover_log) == 1

    reloaded_again = BudgetManager(data_file=data_file)
    budget_again = reloaded_again.get_budget("ops")
    print(f"  Second reload: spent=${budget_again.spent:,.2f}, rollovers logged={len(reloaded_again.rollover_log)}")
    assert budget_again.spent == 0.0
    assert len(reloaded_again.rollover_log) == 1  # not re-applied a second time


def test_unrecognized_period_name_never_auto_rolls():
    """A `period` string outside PERIOD_LENGTHS_DAYS (e.g. a typo, or a
    cadence this project hasn't defined yet) must never fire a rollover by
    guessing a length for it - same absence-means-never-computed rule this
    module already follows elsewhere (see earliest_relief())."""
    print_section("8. An Unrecognized Period Name Never Rolls")

    budgets = BudgetManager(data_file="data/test_budgets_unrecognized.json")
    budget = budgets.allocate("legal", 2000, period="fiscal_year")
    budgets.request_expense("legal", 1900, category="labor")
    budget.period_start = (datetime.utcnow() - timedelta(days=3650)).isoformat()

    assert budget.period_elapsed() is False
    assert budget.days_until_rollover() is None

    still_spent = budgets.get_budget("legal")
    print(f"  spent after a decade-old period_start: ${still_spent.spent:,.2f} (unchanged)")
    assert still_spent.spent == 1900
    assert len(budgets.rollover_log) == 0


def test_org_summary_rolls_every_department_it_reports_on():
    """organization_summary()/over_budget_departments() read self.budgets
    directly rather than one get_budget() call at a time - confirms they
    still pick up an overdue rollover rather than reporting stale spend."""
    print_section("9. Org-Wide Rollups Also Apply Overdue Rollovers")

    budgets = BudgetManager(data_file="data/test_budgets_org_summary.json")
    budget = budgets.allocate("hr", 1000, period="daily")
    budgets.request_expense("hr", 950, category="labor")
    budget.period_start = (datetime.utcnow() - timedelta(days=2)).isoformat()

    summary = budgets.organization_summary()
    print(f"  total_spent after org_summary(): ${summary['total_spent']:,.2f}")
    assert summary["total_spent"] == 0.0  # hr's overdue spend was rolled away, not counted

    over_budget = budgets.over_budget_departments(threshold=0.5)
    print(f"  over_budget_departments(): {over_budget}")
    assert "hr" not in over_budget  # fresh period, 0% utilized


def main():
    print("\n" + "=" * 60)
    print("  BUDGET & CAPACITY MANAGEMENT DEMONSTRATION")
    print("=" * 60)

    test_budget_allocation_and_spend()
    test_reservation_flow()
    test_org_summary_and_alerts()
    test_decision_bridge()
    test_capacity_management()
    test_period_rollover_resets_spend_but_not_reservations()
    test_period_rollover_persists_across_reload()
    test_unrecognized_period_name_never_auto_rolls()
    test_org_summary_rolls_every_department_it_reports_on()

    print("\n" + "=" * 60)
    print("  [OK] All budget & capacity tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_budgets.json", "data/test_agent_states.json",
              "data/test_budgets_rollover.json", "data/test_budgets_reload.json",
              "data/test_budgets_unrecognized.json", "data/test_budgets_org_summary.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
