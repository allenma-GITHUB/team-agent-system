#!/usr/bin/env python3
"""
Test that TaskExecutor.get_agent_for_department()/AgentRegistry.register()
don't race on FIRST creation of an agent for a brand-new department.

Real bug found while stress-testing the previous checkpoint's per-agent
lock fix: that fix only protects work funneled through one
DepartmentHeadAgent object. get_agent_for_department()'s own
"if department not in self.agents: self.agents[department] = ..." was a
plain check-then-act with no lock - two worker threads racing to process
the FIRST task for a never-before-seen department could each see "not
cached yet" and each construct their own separate DepartmentHeadAgent,
each with its own separate lock. Two objects for "the same" department
don't share a lock, so the earlier per-agent-lock fix did nothing to
protect them from each other. AgentRegistry.register() had the identical
check-then-act pattern underneath.

Confirmed with a concrete repro: 15 brand-new departments, 5 tasks each,
16 worker threads - failed roughly 1 in 4 runs (a department's own
tasks_completed short by exactly the number of racing first-creations,
or the shared expense_log missing an entry) before this fix; 15/15 clean
runs after it.
"""
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import TaskExecutor
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _isolated_executor(suffix: str, max_workers: int):
    registry = AgentRegistry(data_file=f"data/test_cfc_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_cfc_budgets_{suffix}.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file=f"data/test_cfc_metrics_{suffix}.json")
    executor = TaskExecutor(
        LLMProvider(), max_workers=max_workers,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    )
    return executor, registry, budgets, analytics


def test_many_brand_new_departments_created_concurrently_stay_correct():
    """15 departments never seen before, 5 tasks each, 16 workers - every
    department should end with exactly 5 recorded completions and the
    shared expense_log should have exactly 75 entries, run after run."""
    print_section("1. Many Brand-New Departments Created Concurrently")

    n_depts = 15
    tasks_per_dept = 5

    for run_num in range(5):
        executor, registry, budgets, analytics = _isolated_executor(f"run{run_num}", max_workers=16)
        for d in range(n_depts):
            budgets.allocate(f"dept_{d}", 1_000_000)

        tasks = [(f"dept_{d}", f"task {i}", 1.0)
                 for d in range(n_depts) for i in range(tasks_per_dept)]
        results = executor.execute_parallel(tasks)

        errors = [r for r in results if "error" in r]
        total_completed = sum(
            analytics.get_agent_metrics(f"dept_{d}_head").tasks_completed for d in range(n_depts)
        )

        print(f"  Run {run_num + 1}: errors={len(errors)}, total_completed={total_completed} "
              f"(expected {n_depts * tasks_per_dept}), expense_log={len(budgets.expense_log)}")

        assert errors == []
        assert total_completed == n_depts * tasks_per_dept
        assert len(budgets.expense_log) == n_depts * tasks_per_dept

        # And every department individually got exactly its share - not just the total
        for d in range(n_depts):
            completed = analytics.get_agent_metrics(f"dept_{d}_head").tasks_completed
            assert completed == tasks_per_dept, f"dept_{d} got {completed}, expected {tasks_per_dept}"


def test_get_agent_for_department_returns_the_same_object_under_contention():
    """Directly stress get_agent_for_department() itself: many threads
    asking for the same never-before-seen department should all get back
    the identical object, not several different ones."""
    print_section("2. get_agent_for_department() Returns One Object Under Contention")

    import concurrent.futures

    executor, registry, budgets, analytics = _isolated_executor("direct", max_workers=32)
    budgets.allocate("brand_new_dept", 100000)

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        agents = list(pool.map(lambda _: executor.get_agent_for_department("brand_new_dept"), range(50)))

    distinct_objects = {id(a) for a in agents}
    print(f"  Distinct DepartmentHeadAgent objects returned across 50 concurrent calls: {len(distinct_objects)}")
    assert len(distinct_objects) == 1


def main():
    print("\n" + "=" * 60)
    print("  CONCURRENT FIRST-CREATION REGRESSION TEST")
    print("=" * 60)

    test_many_brand_new_departments_created_concurrently_stay_correct()
    test_get_agent_for_department_returns_the_same_object_under_contention()

    print("\n" + "=" * 60)
    print("  [OK] All concurrent first-creation tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_cfc_*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
