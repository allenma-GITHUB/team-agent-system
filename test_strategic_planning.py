#!/usr/bin/env python3
"""
Test StrategicPlanner: turns budget utilization signals (already computed
by budgets.py) into concrete reallocation proposals and, on request, applies
them. Before this, a department stuck at 95% utilization stayed there
forever even while another sat at 5% - nothing ever acted on the signal.
Uses isolated BudgetManager instances throughout, matching today's DI pattern.
"""
from budgets import BudgetManager, CapacityManager
from agent_state import AgentRegistry
from strategy import StrategicPlanner


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_proposes_reallocation_from_underspender_to_overspender():
    """One department at 5% utilization, one at 95% - expect a proposal
    moving money from the first to the second."""
    print_section("1. Proposes Reallocation From Underspender To Overspender")

    budgets = BudgetManager(data_file="data/test_strategy_budgets.json")
    budgets.allocate("qa_strategy_idle", 10000)
    budgets.request_expense("qa_strategy_idle", 500, category="labor")  # 5% utilized

    budgets.allocate("qa_strategy_busy", 10000)
    budgets.request_expense("qa_strategy_busy", 9500, category="labor")  # 95% utilized

    planner = StrategicPlanner(budgets=budgets, capacity=CapacityManager(registry=AgentRegistry(
        data_file="data/test_strategy_agents.json"
    )))
    proposals = planner.propose_reallocations(low_threshold=0.3, high_threshold=0.85)

    print(f"  Proposals: {len(proposals)}")
    for p in proposals:
        print(f"    {p.from_department} -> {p.to_department}: ${p.amount:,.2f} ({p.reason})")

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.from_department == "qa_strategy_idle"
    assert proposal.to_department == "qa_strategy_busy"
    # busy needs: 9500 / 0.85 - 10000 = 11176.47 - 10000 = 1176.47
    assert abs(proposal.amount - 1176.47) < 1.0


def test_never_drains_a_donor_below_its_reserve():
    """A donor should never be proposed to give away more than
    (available - reserve_pct * allocated)."""
    print_section("2. Donor Never Drained Below Its Reserve")

    budgets = BudgetManager(data_file="data/test_strategy_budgets2.json")
    budgets.allocate("qa_strategy_small_donor", 1000)
    budgets.request_expense("qa_strategy_small_donor", 50, category="labor")  # 5% utilized, $950 available

    budgets.allocate("qa_strategy_huge_need", 10000)
    budgets.request_expense("qa_strategy_huge_need", 9900, category="labor")  # 99% utilized, huge shortfall

    planner = StrategicPlanner(budgets=budgets, reserve_pct=0.2)
    proposals = planner.propose_reallocations(low_threshold=0.3, high_threshold=0.85)

    print(f"  Proposals: {len(proposals)}")
    for p in proposals:
        print(f"    {p.from_department} -> {p.to_department}: ${p.amount:,.2f}")

    assert len(proposals) == 1
    # Max giveable: 950 available - 0.2 * 1000 reserve = 750
    assert abs(proposals[0].amount - 750) < 1.0


def test_no_proposals_when_nothing_crosses_thresholds():
    """Two departments both sitting in the middle should generate nothing."""
    print_section("3. No Proposals When Nothing Crosses Thresholds")

    budgets = BudgetManager(data_file="data/test_strategy_budgets3.json")
    budgets.allocate("qa_strategy_normal_a", 10000)
    budgets.request_expense("qa_strategy_normal_a", 5000, category="labor")  # 50%
    budgets.allocate("qa_strategy_normal_b", 10000)
    budgets.request_expense("qa_strategy_normal_b", 4000, category="labor")  # 40%

    planner = StrategicPlanner(budgets=budgets)
    proposals = planner.propose_reallocations()

    print(f"  Proposals: {len(proposals)}")
    assert proposals == []


def test_apply_reallocations_actually_moves_allocated_budget():
    """apply_reallocations() should mutate allocated on both sides and persist."""
    print_section("4. apply_reallocations() Moves Allocated Budget")

    budgets = BudgetManager(data_file="data/test_strategy_budgets4.json")
    budgets.allocate("qa_strategy_apply_donor", 10000)
    budgets.request_expense("qa_strategy_apply_donor", 200, category="labor")  # 2%
    budgets.allocate("qa_strategy_apply_recipient", 10000)
    budgets.request_expense("qa_strategy_apply_recipient", 9600, category="labor")  # 96%

    planner = StrategicPlanner(budgets=budgets)
    proposals = planner.rebalance()  # propose + apply in one call

    donor = budgets.get_budget("qa_strategy_apply_donor")
    recipient = budgets.get_budget("qa_strategy_apply_recipient")
    print(f"  Donor allocated after rebalance: ${donor.allocated:,.2f} (was $10,000)")
    print(f"  Recipient allocated after rebalance: ${recipient.allocated:,.2f} (was $10,000)")

    assert len(proposals) == 1
    moved = proposals[0].amount
    assert donor.allocated == 10000 - moved
    assert recipient.allocated == 10000 + moved

    # And it persisted - a fresh BudgetManager reading the same file sees it too.
    reloaded = BudgetManager(data_file="data/test_strategy_budgets4.json")
    assert reloaded.get_budget("qa_strategy_apply_donor").allocated == donor.allocated
    assert reloaded.get_budget("qa_strategy_apply_recipient").allocated == recipient.allocated


def main():
    print("\n" + "=" * 60)
    print("  STRATEGIC BUDGET REALLOCATION DEMONSTRATION")
    print("=" * 60)

    test_proposes_reallocation_from_underspender_to_overspender()
    test_never_drains_a_donor_below_its_reserve()
    test_no_proposals_when_nothing_crosses_thresholds()
    test_apply_reallocations_actually_moves_allocated_budget()

    print("\n" + "=" * 60)
    print("  [OK] All strategic-planning tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_strategy_budgets.json", "data/test_strategy_budgets2.json",
              "data/test_strategy_budgets3.json", "data/test_strategy_budgets4.json",
              "data/test_strategy_agents.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
