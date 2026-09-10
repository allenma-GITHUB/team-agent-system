#!/usr/bin/env python3
"""
Regression test for a real correctness bug found while auditing the
workflow-execution bridge (checkpoints 2/4 added budget+capacity gating
to DepartmentHeadAgent.run(), which can return status="escalated" and
approved=False instead of doing the work): TaskExecutor.execute() threw
that status away entirely and unconditionally built
`{"summary": f"{department} team completed task in {duration}s", ...}`
with no "status"/"approved" key at all.

Two real call sites trusted that fabricated cheerfulness:
  - main_v2.process_tasks() decided "completed" vs "failed" purely by
    checking `"error" not in result` - a key that's only ever set when a
    worker thread *crashes* (execute_parallel's except Exception branch).
    A department that declined a task because it couldn't afford it, or
    had no capacity, or the decision engine escalated it, was reported
    "✓ ... completed" and permanently marked "completed" in tasks.json -
    identical to a task that was genuinely done.
  - workflows.py's execute_step()/complete_step() marked a workflow step
    COMPLETED the same way, regardless of whether the department actually
    did the work.

Confirmed with a concrete repro: draining a department's budget to $0,
then running a task through it via TaskExecutor.execute(), returned a
result with no trace of the escalation and process_tasks() recorded it
as "completed".

Fixed: TaskExecutor.execute() now passes through the real `status`/
`approved` from the agent's result and builds an honest summary; main_v2.
process_tasks() (both the parallel and sequential paths) now maps that
status to "escalated" instead of "completed" via a shared
_task_status_for_result() helper.

(workflows.py's execute_step() still marks BLOCKED-by-budget steps as
COMPLETED when the underlying task execution is escalated for a
different reason, e.g. capacity - that's a separate, deeper fix left for
a future checkpoint; this one closes the more common and more damaging
gap in the plain task queue that main_v2.py process/list/show/status
all read from.)
"""
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

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


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
    registry = AgentRegistry(data_file=f"data/test_tev_agents_{suffix}.json")
    budgets = BudgetManager(data_file=f"data/test_tev_budgets_{suffix}.json")
    capacity = CapacityManager(registry=registry)
    analytics = PerformanceAnalytics(data_file=f"data/test_tev_metrics_{suffix}.json")
    executor = TaskExecutor(
        LLMProvider(), agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=capacity, analytics=analytics
    )
    return executor, budgets


def test_execute_reports_escalated_status_when_budget_denied():
    print_section("1. TaskExecutor.execute() Reports status='escalated', Not a Fake Completion")

    executor, budgets = _isolated_executor("execute")
    budgets.allocate("droughtdept", 100)  # tiny budget, easily exhausted
    budgets.get_budget("droughtdept").spent = 100  # fully drained already

    result = executor.execute("droughtdept", "do something", estimated_hours=1.0)
    print(f"  result: status={result.get('status')!r} approved={result.get('approved')!r}")
    print(f"  summary: {result['summary']!r}")

    assert result.get("status") == "escalated"
    assert result.get("approved") is False
    assert "escalated" in result["summary"].lower()
    assert budgets.get_budget("droughtdept").spent == 100  # never billed further

    for f in pathlib.Path("data").glob("test_tev_*execute*"):
        f.unlink(missing_ok=True)


def test_execute_still_reports_completed_for_real_work():
    print_section("2. TaskExecutor.execute() Still Reports 'completed' For Real Work")

    executor, budgets = _isolated_executor("execute_ok")
    budgets.allocate("flushdept", 1_000_000)

    result = executor.execute("flushdept", "do something affordable", estimated_hours=1.0)
    print(f"  result: status={result.get('status')!r} approved={result.get('approved')!r}")

    assert result.get("status") == "completed"
    assert result.get("approved") is True
    assert budgets.get_budget("flushdept").spent > 0

    for f in pathlib.Path("data").glob("test_tev_*execute_ok*"):
        f.unlink(missing_ok=True)


def test_execute_parallel_preserves_escalation_per_task():
    print_section("3. execute_parallel() Preserves Per-Task Escalation, Doesn't Collapse to 'error'")

    executor, budgets = _isolated_executor("parallel")
    budgets.allocate("richdept", 1_000_000)
    budgets.allocate("poordept", 100)
    budgets.get_budget("poordept").spent = 100

    results = executor.execute_parallel([
        ("richdept", "affordable task", 1.0),
        ("poordept", "unaffordable task", 1.0),
    ])
    print(f"  richdept -> status={results[0].get('status')!r}")
    print(f"  poordept -> status={results[1].get('status')!r}")

    assert "error" not in results[0] and "error" not in results[1]
    assert results[0]["status"] == "completed"
    assert results[1]["status"] == "escalated"

    for f in pathlib.Path("data").glob("test_tev_*parallel*"):
        f.unlink(missing_ok=True)


def test_process_tasks_marks_escalated_tasks_escalated_not_completed_parallel():
    print_section("4. CLI process_tasks(parallel=True) Marks Escalation Honestly")

    with IsolatedCwd():
        main_v2.budget_manager.allocate("cli_drought_p", 100)
        main_v2.budget_manager.get_budget("cli_drought_p").spent = 100

        main_v2.submit_task("will be escalated", department="cli_drought_p", estimated_hours=1.0)
        _, output = _captured(main_v2.process_tasks, parallel=True)
        print(output)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        print(f"  task status: {tasks[0]['status']!r}")
        assert tasks[0]["status"] == "escalated"
        assert "escalated" in output.lower()
        assert "✓ Task" not in output  # must not claim success


def test_process_tasks_marks_escalated_tasks_escalated_not_completed_sequential():
    print_section("5. CLI process_tasks(parallel=False) Marks Escalation Honestly")

    with IsolatedCwd():
        main_v2.budget_manager.allocate("cli_drought_s", 100)
        main_v2.budget_manager.get_budget("cli_drought_s").spent = 100

        main_v2.submit_task("will be escalated", department="cli_drought_s", estimated_hours=1.0)
        _, output = _captured(main_v2.process_tasks, parallel=False)
        print(output)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        print(f"  task status: {tasks[0]['status']!r}")
        assert tasks[0]["status"] == "escalated"
        assert "escalated" in output.lower()
        assert "✓ Task" not in output


def main():
    print("\n" + "=" * 60)
    print("  TASK ESCALATION VISIBILITY REGRESSION TEST")
    print("=" * 60)

    test_execute_reports_escalated_status_when_budget_denied()
    test_execute_still_reports_completed_for_real_work()
    test_execute_parallel_preserves_escalation_per_task()
    test_process_tasks_marks_escalated_tasks_escalated_not_completed_parallel()
    test_process_tasks_marks_escalated_tasks_escalated_not_completed_sequential()

    print("\n" + "=" * 60)
    print("  [OK] All task escalation visibility tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
