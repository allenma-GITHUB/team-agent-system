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
from budgets import budget_manager, capacity_manager


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_default_task_executes_without_approval():
    """A normal, short task shouldn't need approval and should just run."""
    print_section("1. Default Task Executes Without Approval")

    bus = EventBus()
    agent = DepartmentHeadAgent("qa_default", llm_provider=LLMProvider(), bus=bus)
    result = agent.run("Investigate a minor UI glitch")

    print(f"  Status: {result['status']}, Approved: {result['approved']}")
    assert result["status"] == "completed"
    assert result["approved"] is True

    budget_check_events = bus.get_traces("budget_check")
    print(f"  Budget checks performed: {len(budget_check_events)} (none expected)")
    assert len(budget_check_events) == 0


def test_capacity_check_is_emitted():
    """Every run should report a capacity snapshot for its department."""
    print_section("2. Capacity Check Emitted On Every Run")

    bus = EventBus()
    agent = DepartmentHeadAgent("qa_capacity", llm_provider=LLMProvider(), bus=bus)
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
    agent = DepartmentHeadAgent(department, llm_provider=LLMProvider(), bus=bus, cost_per_hour=100)
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
    agent = DepartmentHeadAgent(department, llm_provider=LLMProvider(), bus=bus, cost_per_hour=100)
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


def main():
    print("\n" + "=" * 60)
    print("  RESOURCE-GATED TASK EXECUTION DEMONSTRATION")
    print("=" * 60)

    test_default_task_executes_without_approval()
    test_capacity_check_is_emitted()
    test_high_cost_task_draws_from_budget_when_affordable()
    test_high_cost_task_escalates_when_budget_is_insufficient()

    print("\n" + "=" * 60)
    print("  [OK] All resource-gating tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
