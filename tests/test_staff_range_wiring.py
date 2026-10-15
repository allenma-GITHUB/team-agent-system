#!/usr/bin/env python3
"""
config.json's agents.*_head.staff_range (e.g. [5, 15] for engineering) has
been declared since this project's very first commit (d894e20) and had
zero consumers anywhere - confirmed with `grep -rn "staff_range"
--include=*.py .` returning nothing but the field's own JSON entries.
get_staff_count() returned departments.json's flat `staff` number
regardless of it, so DepartmentHeadAgent's simulated staff_contributions
count never changed no matter how booked a department's real agents were.

Confirmed live before this fix: a fresh engineering department run at 0%
utilization and the same department run at 90% utilization produced the
identical staff_contributions count (5 either way), despite
engineering_head's own config declaring a [5, 15] staffing band for
exactly this.

This pins the fix:
1. DepartmentManager.get_staff_range() gives the field a real reader -
   looked up from the matching "{department}_head" agents entry, None
   when no such entry (or no staff_range key) exists, never a fabricated
   [0, 0].
2. DepartmentHeadAgent._effective_staff_count() flexes the flat baseline
   within that declared band by real CapacitySnapshot.utilization_pct() -
   the same snapshot already computed for the capacity_check trace, not
   a second, independent read. At exactly 50% utilization it returns the
   flat baseline unchanged (the "normal load" pivot point); it clamps to
   the declared floor/ceiling at the extremes; and a department with no
   declared range falls back to the flat baseline, same as before this
   fix existed.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from departments import DepartmentManager
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent, TaskExecutor
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager, CapacitySnapshot
from performance import PerformanceAnalytics

agent_registry = AgentRegistry(data_file="data/test_staffrange_agents.json")
budget_manager = BudgetManager(data_file="data/test_staffrange_budgets.json")
capacity_manager = CapacityManager(registry=agent_registry)
analytics = PerformanceAnalytics(data_file="data/test_staffrange_metrics.json")


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _agent(department: str) -> DepartmentHeadAgent:
    return DepartmentHeadAgent(
        department, llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics,
    )


def test_get_staff_range_reads_real_config_and_handles_absence():
    """The producer side: get_staff_range() must actually read config.json's
    declared band, and return None (not [0, 0] or a crash) for a department
    with no matching "{department}_head" agents entry."""
    print_section("1. get_staff_range() Reads Declared Config, None When Absent")

    rng = DepartmentManager.get_staff_range("engineering")
    print(f"  engineering staff_range: {rng}")
    assert rng == (5, 15), "engineering_head's own config.json entry declares [5, 15]"

    missing = DepartmentManager.get_staff_range("a_department_config_json_has_never_heard_of")
    print(f"  undeclared department staff_range: {missing}")
    assert missing is None, "no matching agents entry must mean None, not a fabricated range"


def test_effective_staff_count_pivots_at_fifty_percent_and_clamps_at_the_band():
    """The formula itself, in isolation (no LLM run needed): the flat
    baseline at exactly 50% utilization (today's implicit behavior,
    preserved), scaling toward the declared ceiling above it and the
    declared floor below it, clamped so it can never leave the band."""
    print_section("2. _effective_staff_count() Pivots on the Flat Baseline, Clamps to the Band")

    # A fully isolated, local registry/capacity manager for this test only -
    # not the shared module-level ones tests 3/4 run real tasks through.
    # Registering a second, idle agent here would otherwise linger as a
    # standing delegation candidate for every later test that makes
    # engineering_head look "full and urgent" - should_execute() would then
    # hand the busy engineering task to this leftover agent instead of
    # executing it in engineering, silently invalidating those tests'
    # premise. No computation this test performs (_effective_staff_count()
    # takes a hand-built CapacitySnapshot directly, never calls run()) needs
    # the shared state at all.
    local_registry = AgentRegistry(data_file="data/test_staffrange_formula_agents.json")
    local_capacity = CapacityManager(registry=local_registry)
    local_budgets = BudgetManager(data_file="data/test_staffrange_formula_budgets.json")
    local_analytics = PerformanceAnalytics(data_file="data/test_staffrange_formula_metrics.json")
    local_budgets.allocate("staffrange_formula_dept", 100000)

    agent = DepartmentHeadAgent(
        "staffrange_formula_dept", llm_provider=LLMProvider(),
        agent_state_registry=local_registry, budget_manager=local_budgets,
        capacity_manager=local_capacity, analytics=local_analytics,
    )
    baseline = DepartmentManager.get_staff_count("staffrange_formula_dept")
    print(f"  flat baseline (no declared range for this test department): {baseline}")

    # This test department has no config.json "agents" entry at all, so
    # _effective_staff_count() must fall back to the flat baseline
    # regardless of utilization - the "no declared range" path.
    idle_snap = CapacitySnapshot(department="staffrange_formula_dept", agent_count=1,
                                  total_capacity=10, current_workload=0)
    busy_snap = CapacitySnapshot(department="staffrange_formula_dept", agent_count=1,
                                  total_capacity=10, current_workload=9)
    assert agent._effective_staff_count(idle_snap) == baseline
    assert agent._effective_staff_count(busy_snap) == baseline
    print(f"  no declared range: idle={agent._effective_staff_count(idle_snap)}, "
          f"busy={agent._effective_staff_count(busy_snap)} (both == flat baseline, as before this fix)")

    # engineering has a real declared range: [5, 15], flat baseline 5.
    eng_agent = _agent("engineering")
    eng_baseline = DepartmentManager.get_staff_count("engineering")
    low, high = DepartmentManager.get_staff_range("engineering")
    assert (eng_baseline, low, high) == (5, 5, 15)

    midpoint_snap = CapacitySnapshot(department="engineering", agent_count=1,
                                      total_capacity=10, current_workload=5)  # exactly 50%
    idle_snap = CapacitySnapshot(department="engineering", agent_count=1,
                                  total_capacity=10, current_workload=0)
    maxed_snap = CapacitySnapshot(department="engineering", agent_count=1,
                                   total_capacity=10, current_workload=10)

    at_mid = eng_agent._effective_staff_count(midpoint_snap)
    at_idle = eng_agent._effective_staff_count(idle_snap)
    at_max = eng_agent._effective_staff_count(maxed_snap)
    print(f"  engineering: idle={at_idle}, 50% utilization={at_mid}, maxed={at_max} "
          f"(band is [{low}, {high}], baseline {eng_baseline})")

    assert at_mid == eng_baseline, "50% utilization must reproduce the flat baseline exactly"
    assert at_idle == low, "0% utilization must bottom out at the declared floor"
    assert at_max == high, "100% utilization must top out at the declared ceiling"


def test_full_run_shows_different_staff_contributions_counts_by_real_load():
    """End-to-end, the repro this checkpoint's fix closes: the same
    department, run through a real DepartmentHeadAgent.run(), must no
    longer report the identical staff_contributions count whether it's
    idle or close to fully booked - and the count must never leave the
    department head's own declared band either direction."""
    print_section("3. A Real run() Now Varies staff_contributions With Real Load")

    eng_agent = DepartmentHeadAgent(
        "engineering", llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics,
    )
    budget_manager.allocate("engineering", 100000)
    state = agent_registry.get(eng_agent.agent_id)
    state.profile.max_concurrent_tasks = 10

    state.current_workload = 0
    r_idle = eng_agent.run("Fix a small typo in the README", estimated_hours=1.0)
    idle_count = len(r_idle["staff_contributions"])

    state.current_workload = 9  # 90%
    r_busy = eng_agent.run("Fix a small typo in the README", estimated_hours=1.0)
    busy_count = len(r_busy["staff_contributions"])

    print(f"  idle run staff_contributions: {idle_count}")
    print(f"  90%-utilization run staff_contributions: {busy_count}")

    low, high = DepartmentManager.get_staff_range("engineering")
    assert low <= idle_count <= high
    assert low <= busy_count <= high
    assert idle_count != busy_count, (
        "this is the exact defect this checkpoint fixes: before it, these "
        "two counts were always identical regardless of real load"
    )
    assert busy_count > idle_count, "more real load must flex staffing up, not down"


def test_execute_wrapper_carries_the_same_scaled_count_through_staff_count_and_execution_steps():
    """TaskExecutor.execute() (what main_v2.py's CLI and web_server.py both
    call) derives its own "staff_count" key from len(execution_steps), and
    "execution_steps" is staff_contributions under another name (see
    execute()'s own comment on why it spreads the raw result first). Both
    downstream keys must reflect the newly-scaled count automatically,
    with no separate wiring needed - proving the fix reaches the CLI/
    dashboard consumer chain that already existed, not just the raw
    DepartmentHeadAgent.run() result."""
    print_section("4. TaskExecutor.execute()'s staff_count/execution_steps Stay In Sync")

    budget_manager.allocate("engineering", 100000)
    executor = TaskExecutor(LLMProvider(), bus=EventBus(),
                             agent_state_registry=agent_registry, budget_manager=budget_manager,
                             capacity_manager=capacity_manager, analytics=analytics)
    eng_agent = executor.get_agent_for_department("engineering")
    state = agent_registry.get(eng_agent.agent_id)
    state.profile.max_concurrent_tasks = 10
    state.current_workload = 9  # 90%, same as the real-load test above

    result = executor.execute("engineering", "Fix a small typo in the README", estimated_hours=1.0)
    print(f"  staff_count: {result['staff_count']}, "
          f"len(execution_steps): {len(result['execution_steps'])}, "
          f"len(staff_contributions): {len(result['staff_contributions'])}")

    assert result["staff_count"] == len(result["execution_steps"])
    assert result["execution_steps"] == result["staff_contributions"]
    low, high = DepartmentManager.get_staff_range("engineering")
    assert low <= result["staff_count"] <= high
    assert result["staff_count"] > DepartmentManager.get_staff_count("engineering"), (
        "90% utilization must have scaled this up past the flat baseline, "
        "visible through the exact keys the CLI and dashboard already read"
    )


def main():
    print("\n" + "=" * 60)
    print("  staff_range GETS A PRODUCER AND A CONSUMER")
    print("=" * 60)

    test_get_staff_range_reads_real_config_and_handles_absence()
    test_effective_staff_count_pivots_at_fifty_percent_and_clamps_at_the_band()
    test_full_run_shows_different_staff_contributions_counts_by_real_load()
    test_execute_wrapper_carries_the_same_scaled_count_through_staff_count_and_execution_steps()

    print("\n" + "=" * 60)
    print("  [OK] All staff_range wiring tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_staffrange_agents.json", "data/test_staffrange_budgets.json",
              "data/test_staffrange_metrics.json", "data/test_staffrange_formula_agents.json",
              "data/test_staffrange_formula_budgets.json", "data/test_staffrange_formula_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
