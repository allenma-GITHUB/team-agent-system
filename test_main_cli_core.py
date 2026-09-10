#!/usr/bin/env python3
"""
Regression tests for main_v2.py's core task pipeline: submit_task(),
process_tasks(), list_tasks(), show_task(), show_status(). Sixteen
checkpoints today built features on top of these functions, but they've
only ever been validated by manual end-to-end CLI runs in a shell - never
by an automated test that would catch a regression on its own. This closes
that gap.

Runs entirely in an isolated tempfile.TemporaryDirectory() with a copy of
the real config.json, so it never touches the actual repo's data/ files.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile

import main_v2

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class IsolatedCwd:
    """Runs a block inside a fresh temp directory with config.json copied
    in, then restores the original cwd - so main_v2's relative data/ paths
    stay isolated from the real repo."""

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
    """Run fn, returning (result, everything it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def test_submit_task_routes_and_estimates_correctly():
    print_section("1. submit_task() Routes Department And Estimates Hours")

    with IsolatedCwd():
        task_id = main_v2.submit_task("Fix a login bug")  # no --dept, no --hours
        tasks = json.loads(main_v2.TASKS_FILE.read_text())

        print(f"  Task: {tasks[0]}")
        assert len(tasks) == 1
        assert tasks[0]["id"] == task_id
        assert tasks[0]["department"] == "engineering"  # routed from "bug" keyword
        assert tasks[0]["estimated_hours"] == 1.0  # neutral description -> default
        assert tasks[0]["status"] == "queued"
        assert tasks[0]["result"] is None


def test_process_tasks_executes_queued_tasks_and_marks_them_completed():
    print_section("2. process_tasks() Executes And Completes Queued Tasks")

    with IsolatedCwd():
        main_v2.submit_task("Investigate a checkout error", department="engineering")
        main_v2.submit_task("Redesign the settings page", department="design")

        _, output = _captured(main_v2.process_tasks, parallel=True)
        print(output)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        assert all(t["status"] == "completed" for t in tasks)
        assert all(t["result"] is not None for t in tasks)
        assert all(t["completed_at"] is not None for t in tasks)
        assert "Processing 2 task(s)" in output
        assert "All tasks completed" in output


def test_process_tasks_sequential_path_also_completes_tasks():
    print_section("3. process_tasks(parallel=False) Also Completes Tasks")

    with IsolatedCwd():
        main_v2.submit_task("A single task", department="engineering")
        _, output = _captured(main_v2.process_tasks, parallel=False)
        print(output)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        assert tasks[0]["status"] == "completed"
        assert "Mode: SEQUENTIAL" in output


def test_process_tasks_with_nothing_queued_is_a_clean_no_op():
    print_section("4. process_tasks() With Nothing Queued")

    with IsolatedCwd():
        _, output = _captured(main_v2.process_tasks)
        print(f"  Output: {output.strip()!r}")
        assert "No queued tasks." in output


def test_list_tasks_filters_by_status():
    print_section("5. list_tasks() Filters By Status")

    with IsolatedCwd():
        main_v2.submit_task("Task A", department="engineering")
        main_v2.submit_task("Task B", department="design")
        main_v2.process_tasks()  # both become "completed"
        main_v2.submit_task("Task C", department="sales")  # stays "queued"

        _, all_output = _captured(main_v2.list_tasks)
        _, queued_output = _captured(main_v2.list_tasks, status="queued")
        _, completed_output = _captured(main_v2.list_tasks, status="completed")

        print(f"  All: 3 rows -> {all_output.count(chr(10))} lines")
        assert "Task A" in all_output and "Task B" in all_output and "Task C" in all_output

        assert "Task C" in queued_output
        assert "Task A" not in queued_output

        assert "Task A" in completed_output and "Task B" in completed_output
        assert "Task C" not in completed_output


def test_show_task_displays_details_and_handles_missing_gracefully():
    print_section("6. show_task() Shows Details Or Reports Missing")

    with IsolatedCwd():
        task_id = main_v2.submit_task("A specific task to inspect", department="research")
        _, output = _captured(main_v2.show_task, task_id)
        print(output)
        assert f"Task: {task_id}" in output
        assert "A specific task to inspect" in output
        assert "Status: queued" in output

        _, missing_output = _captured(main_v2.show_task, "9999")
        print(f"  Missing task: {missing_output.strip()!r}")
        assert "not found" in missing_output


def test_show_status_reflects_real_counts():
    print_section("7. show_status() Reflects Real Task Counts")

    with IsolatedCwd():
        main_v2.submit_task("Queued one", department="engineering")
        main_v2.submit_task("Will be completed", department="engineering")
        main_v2.process_tasks()  # completes both currently-queued tasks
        main_v2.submit_task("Newly queued", department="design")

        _, output = _captured(main_v2.show_status)
        print(output)
        assert "Total Tasks" in output and "3" in output
        assert "Completed" in output


def main():
    print("\n" + "=" * 60)
    print("  main_v2.py CORE CLI PIPELINE REGRESSION TESTS")
    print("=" * 60)

    test_submit_task_routes_and_estimates_correctly()
    test_process_tasks_executes_queued_tasks_and_marks_them_completed()
    test_process_tasks_sequential_path_also_completes_tasks()
    test_process_tasks_with_nothing_queued_is_a_clean_no_op()
    test_list_tasks_filters_by_status()
    test_show_task_displays_details_and_handles_missing_gracefully()
    test_show_status_reflects_real_counts()

    print("\n" + "=" * 60)
    print("  [OK] All main_v2.py core CLI tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
