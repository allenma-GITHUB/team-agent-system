#!/usr/bin/env python3
"""
Test that DepartmentHeadAgent.run() actually consults capacity and budget
before doing (and billing) work, instead of executing unconditionally.

Covers the integration gap called out in prior daily progress notes:
budgets.py/CapacityManager existed but nothing in the execution path used
them, and decide_on_task() was defined but never called from run().
"""
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics

# Isolated instances instead of the shared global singletons - this test
# used to require manual cleanup (data/agent_states.json, data/budgets.json,
# data/performance_metrics.json) after every run; with DI (added in an
# earlier checkpoint) it no longer touches shared state at all.
agent_registry = AgentRegistry(data_file="data/test_rg_agents.json")
budget_manager = BudgetManager(data_file="data/test_rg_budgets.json")
capacity_manager = CapacityManager(registry=agent_registry)
analytics = PerformanceAnalytics(data_file="data/test_rg_metrics.json")


def _agent(department: str, **kwargs) -> DepartmentHeadAgent:
    return DepartmentHeadAgent(
        department, llm_provider=LLMProvider(),
        agent_state_registry=agent_registry, budget_manager=budget_manager,
        capacity_manager=capacity_manager, analytics=analytics, **kwargs
    )


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_default_task_executes_without_approval_but_still_spends():
    """A normal, short task shouldn't need approval - but it still isn't free."""
    print_section("1. Default Task Executes Without Approval, But Still Costs Something")

    department = "qa_default"
    budget_manager.allocate(department, 10000)  # fixed starting point, independent of prior runs

    bus = EventBus()
    agent = _agent(department, bus=bus, cost_per_hour=100)
    result = agent.run("Investigate a minor UI glitch", estimated_hours=1.0)

    print(f"  Status: {result['status']}, Approved: {result['approved']}")
    assert result["status"] == "completed"
    assert result["approved"] is True

    budget_check_events = bus.get_traces("budget_check")
    print(f"  Budget checks performed: {len(budget_check_events)}")
    assert len(budget_check_events) == 1
    assert budget_check_events[0].data["approval_required"] is False

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} (1h * $100/hr = $100 expected, even with no approval)")
    assert budget.spent == 100


def test_capacity_check_is_emitted():
    """Every run should report a capacity snapshot for its department."""
    print_section("2. Capacity Check Emitted On Every Run")

    bus = EventBus()
    agent = _agent("qa_capacity", bus=bus)
    agent.run("Small task")

    events = bus.get_traces("capacity_check")
    print(f"  capacity_check events: {len(events)}")
    assert len(events) == 1
    print(f"  Utilization reported: {events[0].data['utilization']:.0%}, "
          f"agents: {events[0].data['agent_count']}")


def test_high_cost_task_draws_from_budget_when_affordable():
    """A large task needs approval; with enough budget, it executes and spends."""
    print_section("3. High-Cost Task Approved Against a Healthy Budget")

    department = "qa_budget_ok"
    budget_manager.allocate(department, 100000)  # plenty of runway

    bus = EventBus()
    agent = _agent(department, bus=bus, cost_per_hour=100)
    result = agent.run("Major system redesign", estimated_hours=20)

    print(f"  Status: {result['status']}, Approved: {result['approved']}")
    assert result["status"] == "completed"
    assert result["approved"] is True

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} (20h * $100/hr = $2,000 expected)")
    assert budget.spent == 2000


def test_high_cost_task_escalates_when_budget_is_insufficient():
    """The same task, but the department can't afford it -> escalate, don't spend."""
    print_section("4. High-Cost Task Escalates When Budget Is Insufficient")

    department = "qa_budget_denied"
    budget_manager.allocate(department, 500)  # not enough for a 20h task at $100/hr

    bus = EventBus()
    agent = _agent(department, bus=bus, cost_per_hour=100)
    result = agent.run("Major system redesign", estimated_hours=20)

    print(f"  Status: {result['status']}, Approved: {result['approved']}")
    print(f"  Analysis: {result['analysis']}")
    assert result["status"] == "escalated"
    assert result["approved"] is False

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} (should be $0 - no work was done)")
    assert budget.spent == 0

    escalations = bus.get_traces("task_escalated")
    assert len(escalations) == 1
    print(f"  Escalation reasoning: {escalations[0].data['reasoning']}")


def test_routine_task_escalates_once_budget_is_fully_depleted():
    """Even a small, no-approval task can't run once the department is broke."""
    print_section("5. Routine Task Escalates When The Department Is Out Of Money")

    department = "qa_depleted"
    budget_manager.allocate(department, 50)  # less than a single $100 hour of labor

    bus = EventBus()
    agent = _agent(department, bus=bus, cost_per_hour=100)
    result = agent.run("Routine cleanup task", estimated_hours=1.0)

    print(f"  Status: {result['status']}, Approved: {result['approved']}")
    print(f"  Analysis: {result['analysis']}")
    assert result["status"] == "escalated"
    assert result["approved"] is False

    budget = budget_manager.get_budget(department)
    print(f"  Spent: ${budget.spent:,.2f} (should be $0 - budget was depleted before work started)")
    assert budget.spent == 0


def main():
    print("\n" + "=" * 60)
    print("  RESOURCE-GATED TASK EXECUTION DEMONSTRATION")
    print("=" * 60)

    test_default_task_executes_without_approval_but_still_spends()
    test_capacity_check_is_emitted()
    test_high_cost_task_draws_from_budget_when_affordable()
    test_high_cost_task_escalates_when_budget_is_insufficient()
    test_routine_task_escalates_once_budget_is_fully_depleted()

    print("\n" + "=" * 60)
    print("  [OK] All resource-gating tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_rg_agents.json", "data/test_rg_budgets.json", "data/test_rg_metrics.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
