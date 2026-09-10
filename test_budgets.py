#!/usr/bin/env python3
"""
Test budget tracking and capacity management.
Demonstrates department budgets, expense approval, reservation flows,
and staffing capacity reports built on top of the existing agent registry.
"""
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


def main():
    print("\n" + "=" * 60)
    print("  BUDGET & CAPACITY MANAGEMENT DEMONSTRATION")
    print("=" * 60)

    test_budget_allocation_and_spend()
    test_reservation_flow()
    test_org_summary_and_alerts()
    test_decision_bridge()
    test_capacity_management()

    print("\n" + "=" * 60)
    print("  [OK] All budget & capacity tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_budgets.json", "data/test_agent_states.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
