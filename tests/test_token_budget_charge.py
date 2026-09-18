#!/usr/bin/env python3
"""
Regression test: the real token cost of an LLM call now reaches the
department's actual budget, not just agent_state/analytics.

Named as an open "Next Step" for five checkpoints in a row (37-41):
task_executor_v2.py computed mock_cost = tokens_used * 0.00001 AFTER the
LLM call returned, fed it into agent_state.record_performance() and
analytics.record_task(), and then never charged it anywhere near
budgets.py - the ONLY budget charge (request_expense/approve_decision)
happens BEFORE the call, priced purely on estimated_hours * cost_per_hour.
Confirmed live before the fix: a 124-token completed task left
budget.spent exactly equal to the labor charge, $0.00124 short of what
the run actually cost.

Fixed in _run_locked() (task_executor_v2.py): once tokens_used and
mock_cost are known, a second, separate expense (category="llm_tokens")
is charged against the same department budget - real, already-incurred
cost, so it is charged (not gated on) and cannot be refunded or undone if
it fails; failure is recorded (token_cost_charged/token_budget_note) and
emitted (token_budget_check), never silently dropped a second time.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import AGENT_MAX_ITERATIONS, DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class FixedTokenProvider:
    """Answers immediately with a known, fixed token count - no tool calls,
    so the charged amount is exactly predictable."""

    def __init__(self, tokens=120):
        self.tokens = tokens

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        return {
            "content": "Plan complete.", "tool_calls": [],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": self.tokens,
        }


class AlwaysWantsToolsProvider:
    """Never stops asking for tools - the iteration-exhaustion case."""

    def __init__(self, tokens_per_turn=7):
        self.tokens_per_turn = tokens_per_turn
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        return {
            "content": "still thinking",
            "tool_calls": [{"id": f"c{self.turns}", "name": "get_department_budget",
                            "arguments": {"department": "engineering"}}],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": self.tokens_per_turn,
        }


def _agent(provider, suffix, department="engineering", budget=100000, cost_per_hour=100):
    registry = AgentRegistry(data_file=f"data/test_tbc_agents_{suffix}.json")
    state = registry.register(AgentProfile(
        agent_id=f"{department}_head", name=f"{department.title()} Head",
        agent_type="ManagerAgent", department=department,
        expertise_areas=[department], skill_level=4,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    ))
    state.current_workload = 0
    state.learned_preferences = {}
    # register() returns an existing state if a prior run left one on disk;
    # metrics accumulate, so reset them to keep absolute assertions stable.
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_tbc_budgets_{suffix}.json")
    budgets.allocate(department, budget)

    agent = DepartmentHeadAgent(
        department, llm_provider=provider, cost_per_hour=cost_per_hour,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_tbc_metrics_{suffix}.json"),
    )
    return agent, registry, budgets


def test_completed_task_charges_real_token_cost():
    print_section("1. A Completed Task Charges Its Real Token Cost")

    provider = FixedTokenProvider(tokens=120)
    agent, registry, budgets = _agent(provider, "a")

    result = agent.run("Plan the login fix", estimated_hours=2.0)

    assert result["status"] == "completed"
    assert result["tokens_used"] == 120
    expected_token_cost = 120 * 0.00001
    print(f"  token_cost        : {result['token_cost']} (expected {expected_token_cost})")
    assert result["token_cost"] == expected_token_cost
    assert result["token_cost_charged"] is True
    assert result["token_budget_note"] is None

    budget = budgets.get_budget("engineering")
    expected_spent = 2.0 * 100 + expected_token_cost  # labor + token cost
    print(f"  budget.spent      : {budget.spent} (expected {expected_spent})")
    assert budget.spent == expected_spent

    # A real, separate ledger entry - not folded silently into the labor line.
    token_expenses = [e for e in budgets.expense_log if e.category == "llm_tokens"]
    print(f"  llm_tokens entries: {len(token_expenses)}")
    assert len(token_expenses) == 1
    assert token_expenses[0].amount == expected_token_cost
    assert token_expenses[0].agent_id == "engineering_head"


def test_incomplete_run_still_charges_token_cost():
    """Mirrors the labor charge's own convention: an agent that exhausts its
    iteration budget still really spent tokens getting there, and that
    spend is not refunded just because the task is reported escalated."""
    print_section("2. An Escalated (Iteration-Exhausted) Run Still Charges Tokens")

    provider = AlwaysWantsToolsProvider(tokens_per_turn=7)
    agent, registry, budgets = _agent(provider, "b")

    result = agent.run("Task the agent never finishes", estimated_hours=2.0)

    assert result["status"] == "escalated"
    assert provider.turns == AGENT_MAX_ITERATIONS
    expected_tokens = AGENT_MAX_ITERATIONS * 7
    print(f"  tokens_used  : {result['tokens_used']} (expected {expected_tokens})")
    assert result["tokens_used"] == expected_tokens
    assert result["token_cost_charged"] is True

    budget = budgets.get_budget("engineering")
    labor_cost = 2.0 * 100
    print(f"  budget.spent : {budget.spent} (labor {labor_cost} + tokens {result['token_cost']})")
    assert budget.spent == labor_cost + result["token_cost"]


def test_zero_token_run_does_not_log_a_noise_expense():
    """No LLM provider at all -> tokens_used stays 0 -> nothing to charge.
    Must not log a $0.00 expense on every such task."""
    print_section("3. A Zero-Token Run Logs No llm_tokens Expense")

    registry = AgentRegistry(data_file="data/test_tbc_agents_c.json")
    budgets = BudgetManager(data_file="data/test_tbc_budgets_c.json")
    budgets.allocate("engineering", 100000)

    agent = DepartmentHeadAgent(
        "engineering", llm_provider=None, cost_per_hour=100,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_tbc_metrics_c.json"),
    )
    result = agent.run("A task with no LLM configured", estimated_hours=1.0)

    assert result["status"] == "completed"
    assert result["tokens_used"] == 0
    assert result["token_cost"] == 0
    assert result["token_cost_charged"] is True  # nothing to charge, vacuously true

    token_expenses = [e for e in budgets.expense_log if e.category == "llm_tokens"]
    print(f"  llm_tokens entries: {len(token_expenses)} (expected 0)")
    assert token_expenses == []

    budget = budgets.get_budget("engineering")
    print(f"  budget.spent: {budget.spent} (expected 100, labor only)")
    assert budget.spent == 100


def test_insufficient_budget_for_token_cost_is_recorded_not_silently_dropped():
    """The department can afford the labor but not the extra token cost -
    the task already ran and cannot be undone, so it must still be
    reported completed, with the shortfall recorded honestly rather than
    silently eaten a second time."""
    print_section("4. A Budget Too Small For The Token Cost Is Recorded, Not Hidden")

    provider = FixedTokenProvider(tokens=500)  # cost = 0.005
    # Exactly enough for labor (1h * 100 = 100), nothing left over for tokens.
    agent, registry, budgets = _agent(provider, "d", department="engineering",
                                      budget=100, cost_per_hour=100)

    result = agent.run("A task that exhausts the department's budget", estimated_hours=1.0)

    assert result["status"] == "completed"  # the work still happened
    assert result["token_cost_charged"] is False
    print(f"  token_budget_note: {result['token_budget_note']}")
    assert result["token_budget_note"] is not None
    assert "Insufficient budget" in result["token_budget_note"]

    budget = budgets.get_budget("engineering")
    print(f"  budget.spent: {budget.spent} (expected 100, token charge refused)")
    assert budget.spent == 100  # labor only - the failed charge was not applied
    assert [e for e in budgets.expense_log if e.category == "llm_tokens"] == []


def main():
    print("\n" + "=" * 60)
    print("  TOKEN COST REACHES THE REAL BUDGET - REGRESSION TEST")
    print("=" * 60)

    test_completed_task_charges_real_token_cost()
    test_incomplete_run_still_charges_token_cost()
    test_zero_token_run_does_not_log_a_noise_expense()
    test_insufficient_budget_for_token_cost_is_recorded_not_silently_dropped()

    print("\n" + "=" * 60)
    print("  [OK] All token-budget-charge tests passed!")
    print("=" * 60 + "\n")

    import glob
    import pathlib
    for f in glob.glob("data/test_tbc_*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
