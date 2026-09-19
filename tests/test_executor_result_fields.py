#!/usr/bin/env python3
"""
Regression test for a gap named as an open "Next Step" in checkpoint 45's
own notes: TaskExecutor.execute() hand-picked a fixed set of keys
(department, analysis, status, approved, staff_count, execution_steps,
summary, details) out of DepartmentHeadAgent.run()'s result and discarded
everything else - tokens_used, token_cost, token_cost_charged,
token_budget_note, quality_score, used_tools, tool_calls, tool_calls_failed,
llm_provider, metrics, and (on a delegated task) delegation_chain and
friends. Three other test files' own comments document workarounds forced
by this exact gap (reading token cost back from the budget manager's
expense log instead of the executor's return value), because there was
nothing else to read it from.

Confirmed live before the fix, against an isolated executor with a real
(mock-backed) LLM call:

    result.keys() == {"department", "analysis", "status", "approved",
                       "staff_count", "execution_steps", "summary", "details"}
    "tokens_used" in result -> False
    "quality_score" in result -> False

Fixed: execute() now spreads DepartmentHeadAgent.run()'s full result and
layers its own derived keys on top, instead of re-curating (and re-losing)
a hand-picked subset. main_v2.show_task() was updated to actually display
the newly-visible fields, since a field nothing ever reads is worth exactly
as much as a field that never arrived.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile

import main_v2
from task_executor_v2 import TaskExecutor
from agent_state import AgentRegistry
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from llm_provider import LLMProvider

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class IsolatedCwd:
    def __enter__(self):
        self._original_cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        shutil.copy(os.path.join(REPO_ROOT, "config.json"), self._tmpdir)
        os.chdir(self._tmpdir)
        return self._tmpdir

    def __exit__(self, *exc):
        os.chdir(self._original_cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)


def _captured(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def _isolated_executor(suffix: str):
    registry = AgentRegistry(data_file=f"data/test_erf_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_erf_budgets_{suffix}.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file=f"data/test_erf_metrics_{suffix}.json")
    executor = TaskExecutor(
        LLMProvider(), agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    )
    return executor, budgets


def test_execute_passes_per_task_fields_through_for_a_completed_task():
    print_section("1. execute() Surfaces the Fields DepartmentHeadAgent.run() Already Returns")

    executor, budgets = _isolated_executor("completed")
    budgets.allocate("erfdept", 1_000_000)

    result = executor.execute("erfdept", "Investigate a production incident", estimated_hours=1.0)
    print(f"  keys: {sorted(result.keys())}")

    assert result["status"] == "completed"
    # A mock-backed run supports tools (LLMProvider.TOOL_CAPABLE_PROVIDERS
    # includes "mock"), so these are real, non-placeholder values, not just
    # keys that happen to be present with zero/None in them.
    assert result.get("llm_provider") == "mock"
    assert result.get("tokens_used", 0) > 0
    assert result.get("token_cost", 0) > 0
    assert result.get("quality_score", 0) > 0
    assert result.get("used_tools") is True
    assert isinstance(result.get("tool_calls"), int)
    assert isinstance(result.get("metrics"), dict) and "avg_quality" in result["metrics"]

    # Cross-check against the ledger that used to be the only way to see
    # this number at all (see the three test files' comments this
    # checkpoint corrected).
    token_cost_in_ledger = sum(e.amount for e in budgets.expense_log if e.category == "llm_tokens")
    assert result["token_cost"] == token_cost_in_ledger

    for f in pathlib.Path("data").glob("test_erf_*completed*"):
        f.unlink(missing_ok=True)


def test_execute_escalated_before_execution_has_no_stale_execution_fields():
    print_section("2. An Escalated-Before-Execution Result Doesn't Fake Having Run")

    executor, budgets = _isolated_executor("escalated")
    budgets.allocate("erfdrought", 100)
    budgets.get_budget("erfdrought").spent = 100  # fully drained already

    result = executor.execute("erfdrought", "do something", estimated_hours=1.0)

    assert result["status"] == "escalated"
    assert result["approved"] is False
    # Escalated before the LLM call ever ran (budget denial happens first),
    # so these fields were never computed - absent, not zeroed out, and
    # every .get() call site (main_v2.show_task included) must tolerate that.
    assert "tokens_used" not in result
    assert "quality_score" not in result
    print(f"  status={result['status']!r}, tokens_used present: {'tokens_used' in result}")

    for f in pathlib.Path("data").glob("test_erf_*escalated*"):
        f.unlink(missing_ok=True)


def test_execute_parallel_gives_each_task_its_own_fields():
    print_section("3. execute_parallel() Preserves Per-Task Fields, Doesn't Collapse Them")

    executor, budgets = _isolated_executor("parallel")
    budgets.allocate("erfrich", 1_000_000)
    budgets.allocate("erfpoor", 100)
    budgets.get_budget("erfpoor").spent = 100

    results = executor.execute_parallel([
        ("erfrich", "affordable task", 1.0),
        ("erfpoor", "unaffordable task", 1.0),
    ])

    assert results[0]["status"] == "completed"
    assert results[0].get("tokens_used", 0) > 0
    assert results[1]["status"] == "escalated"
    assert "tokens_used" not in results[1]

    for f in pathlib.Path("data").glob("test_erf_*parallel*"):
        f.unlink(missing_ok=True)


def test_show_task_cli_now_prints_the_previously_invisible_fields():
    print_section("4. CLI show_task() Actually Displays What execute() Now Returns")

    with IsolatedCwd():
        main_v2.budget_manager.allocate("cli_erf", 1_000_000)

        main_v2.submit_task("Investigate a production incident", department="cli_erf", estimated_hours=1.0)
        _captured(main_v2.process_tasks, parallel=False)

        _, output = _captured(main_v2.show_task, "0001")
        print(output)

        assert "LLM Provider:" in output
        assert "Tokens Used:" in output


def main():
    print("\n" + "=" * 60)
    print("  EXECUTOR RESULT FIELDS REGRESSION TEST")
    print("=" * 60)

    test_execute_passes_per_task_fields_through_for_a_completed_task()
    test_execute_escalated_before_execution_has_no_stale_execution_fields()
    test_execute_parallel_gives_each_task_its_own_fields()
    test_show_task_cli_now_prints_the_previously_invisible_fields()

    print("\n" + "=" * 60)
    print("  [OK] All executor result field tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
