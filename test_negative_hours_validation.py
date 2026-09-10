#!/usr/bin/env python3
"""
Regression test for a real bug found while probing main_v2.py's CLI with
edge-case inputs: `submit "test" --dept engineering --hours -5` was
accepted silently as "est. -5.0h". That value flows straight into
task_executor_v2.py's budget check as
`amount = estimated_hours * self.cost_per_hour`, and BudgetManager.
request_expense() did `budget.spent += amount` with no sign check -
a negative amount *decreases* recorded spend instead of increasing it,
manufacturing available budget out of thin air rather than raising an
error. The same held for reserve_funds()/DepartmentBudget.reserve(),
whose can_afford() check (`amount <= available()`) trivially passes any
negative amount.

Fixed at three points:
  1. main_v2.py's CLI `--hours` parsing rejects a negative value before
     ever calling submit_task().
  2. main_v2.submit_task() itself rejects a negative estimated_hours,
     so any other caller (tests, a future API) is covered too.
  3. budgets.py's BudgetManager.request_expense()/reserve_funds() reject
     a negative amount outright - defense in depth, since those are the
     actual point where money would be corrupted regardless of what fed
     them a bad number.

Runs in an isolated tempfile.TemporaryDirectory() (main_v2 pieces) plus
isolated BudgetManager data files (budgets pieces) - never touches the
real repo's data/ files.
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

import main_v2
from budgets import BudgetManager

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


def test_submit_task_rejects_negative_estimated_hours():
    print_section("1. submit_task() Rejects Negative estimated_hours")

    with IsolatedCwd():
        task_id, output = _captured(
            main_v2.submit_task, "test", "engineering", estimated_hours=-5.0
        )
        print(output)

        assert task_id is None
        assert "must be >= 0" in output
        assert not main_v2.TASKS_FILE.exists() or json.loads(main_v2.TASKS_FILE.read_text()) == []


def test_submit_task_still_accepts_zero_and_positive_hours():
    print_section("2. submit_task() Still Accepts Zero And Positive Hours")

    with IsolatedCwd():
        tid_zero = main_v2.submit_task("free task", "engineering", estimated_hours=0.0)
        tid_pos = main_v2.submit_task("real task", "engineering", estimated_hours=3.0)
        tasks = json.loads(main_v2.TASKS_FILE.read_text())

        assert tid_zero is not None and tid_pos is not None
        assert [t["estimated_hours"] for t in tasks] == [0.0, 3.0]


def test_cli_submit_rejects_negative_hours_flag():
    print_section("3. CLI `submit ... --hours -5` Is Rejected Before Queuing")

    with IsolatedCwd():
        old_argv = sys.argv
        try:
            sys.argv = ["main_v2.py", "submit", "test", "--dept", "engineering", "--hours", "-5"]
            _, output = _captured(main_v2.main)
        finally:
            sys.argv = old_argv

        print(output)
        assert "must be >= 0" in output
        assert not main_v2.TASKS_FILE.exists()


def test_budget_manager_rejects_negative_expense_and_reservation():
    print_section("4. BudgetManager Rejects Negative Amounts At The Money Boundary")

    budgets = BudgetManager(data_file="data/test_negative_hours_budgets.json")
    budgets.allocate("engineering", 10000)

    approved, reason = budgets.request_expense("engineering", -500, category="labor")
    print(f"  request_expense(-500): approved={approved} ({reason})")
    assert not approved
    assert "negative" in reason.lower()
    assert budgets.get_budget("engineering").spent == 0.0

    approved, reason = budgets.reserve_funds("engineering", "ref-1", -200)
    print(f"  reserve_funds(-200): approved={approved} ({reason})")
    assert not approved
    assert "negative" in reason.lower()
    assert budgets.get_budget("engineering").reserved_total() == 0.0

    # Legitimate positive amounts still work after the guard.
    approved, _ = budgets.request_expense("engineering", 500, category="labor")
    assert approved
    assert budgets.get_budget("engineering").spent == 500

    pathlib.Path("data/test_negative_hours_budgets.json").unlink(missing_ok=True)


def main():
    print("\n" + "=" * 60)
    print("  NEGATIVE HOURS / NEGATIVE BUDGET AMOUNT REGRESSION TEST")
    print("=" * 60)

    test_submit_task_rejects_negative_estimated_hours()
    test_submit_task_still_accepts_zero_and_positive_hours()
    test_cli_submit_rejects_negative_hours_flag()
    test_budget_manager_rejects_negative_expense_and_reservation()

    print("\n" + "=" * 60)
    print("  [OK] All negative-hours/negative-amount tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
