#!/usr/bin/env python3
"""
Test that DepartmentHeadAgent (and TaskExecutor) can be constructed with
fully isolated dependencies - a private AgentRegistry, BudgetManager,
CapacityManager, and PerformanceAnalytics - and touch none of the shared
data/*.json files the rest of the test suite has to clean up after every
run (data/agent_states.json, data/budgets.json, data/performance_metrics.json).

Every prior checkpoint today that exercised DepartmentHeadAgent needed a
"git checkout -- data/agent_states.json; rm -f data/budgets.json ..." step
afterward. This is what makes that no longer required, for tests written
against the injected form.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hashlib
from pathlib import Path

from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent, TaskExecutor
from agent_state import AgentRegistry, AgentProfile
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics

SHARED_FILES = [
    Path("data/agent_states.json"),
    Path("data/budgets.json"),
    Path("data/performance_metrics.json"),
]

# TaskExecutor's on-demand agent gets DepartmentHeadAgent's fallback profile
# (ManagerAgent, skill_level 3) with no explicit cost_per_hour, so it bills
# at that profile's derived rate - see agent_state.AgentProfile.hourly_rate().
FALLBACK_RATE = AgentProfile(
    agent_id="rate_probe", name="", agent_type="ManagerAgent", department="x",
    expertise_areas=[], skill_level=3, capabilities=[], constraints=[]
).hourly_rate()


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _snapshot(paths):
    """Hash + mtime of each shared file, so we can prove none of them changed."""
    snap = {}
    for p in paths:
        if p.exists():
            snap[p] = (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        else:
            snap[p] = None  # doesn't exist - and must still not exist afterward
    return snap


def test_injected_department_head_touches_no_shared_files():
    """A fully-injected DepartmentHeadAgent should leave every shared data
    file byte-for-byte (or absent-for-absent) unchanged."""
    print_section("1. Injected DepartmentHeadAgent Touches Zero Shared Files")

    before = _snapshot(SHARED_FILES)

    registry = AgentRegistry(data_file="data/test_di_agents.json")
    budgets = BudgetManager(data_file="data/test_di_budgets.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file="data/test_di_metrics.json")
    budgets.allocate("qa_di_dept", 10000)

    bus = EventBus()
    agent = DepartmentHeadAgent(
        "qa_di_dept", llm_provider=LLMProvider(), bus=bus, cost_per_hour=100,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    )
    result = agent.run("An isolated task", estimated_hours=3.0)
    assert result["status"] == "completed"

    after = _snapshot(SHARED_FILES)
    for path in SHARED_FILES:
        print(f"  {path}: unchanged = {before[path] == after[path]}")
        assert before[path] == after[path], f"{path} was touched by an isolated run"

    # But the isolated instances DID record the work:
    assert budgets.get_budget("qa_di_dept").spent == 300  # 3h * $100/hr
    assert registry.get("qa_di_dept_head") is not None
    assert analytics.get_agent_metrics("qa_di_dept_head") is not None
    print("  Isolated instances correctly recorded the spend, agent state, and metrics")


def test_injected_task_executor_propagates_to_every_agent_it_creates():
    """TaskExecutor should pass its injected dependencies to every
    DepartmentHeadAgent it creates on demand, not just one constructed by hand."""
    print_section("2. TaskExecutor Propagates Injected Dependencies")

    before = _snapshot(SHARED_FILES)

    registry = AgentRegistry(data_file="data/test_di_agents2.json")
    budgets = BudgetManager(data_file="data/test_di_budgets2.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file="data/test_di_metrics2.json")
    budgets.allocate("qa_di_engineering", 10000)

    bus = EventBus()
    executor = TaskExecutor(
        LLMProvider(), bus=bus, max_workers=2,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    )
    executor.execute("qa_di_engineering", "A task routed through the executor", estimated_hours=2.0)

    after = _snapshot(SHARED_FILES)
    for path in SHARED_FILES:
        assert before[path] == after[path], f"{path} was touched by an isolated executor run"

    assert budgets.get_budget("qa_di_engineering").spent == 2.0 * FALLBACK_RATE
    print("  Agent created on-demand by TaskExecutor used the injected budget manager, not the global one")


def main():
    print("\n" + "=" * 60)
    print("  DEPARTMENT HEAD / TASK EXECUTOR DEPENDENCY INJECTION")
    print("=" * 60)

    test_injected_department_head_touches_no_shared_files()
    test_injected_task_executor_propagates_to_every_agent_it_creates()

    print("\n" + "=" * 60)
    print("  [OK] All isolation tests passed!")
    print("=" * 60 + "\n")

    for path in [
        "data/test_di_agents.json", "data/test_di_budgets.json", "data/test_di_metrics.json",
        "data/test_di_agents2.json", "data/test_di_budgets2.json", "data/test_di_metrics2.json",
    ]:
        Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
