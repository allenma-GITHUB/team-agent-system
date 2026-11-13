#!/usr/bin/env python3
"""
Checkpoint 96. main_v2.show_status()'s `stats` dict only ever counted two of
a task's four possible `status` values ("queued", "completed") - "escalated"
and "failed" (the other two _task_status_for_result() can produce, see
main_v2.py:91-103) were computed, persisted to tasks.json, and then silently
dropped from the one CLI view whose whole job is "show me task status at a
glance." web_server.py's build_status_payload() - whose own docstring says
it "Mirrors main_v2.show_status()" - already counted all four, so the right
numbers and the right shape already existed one file away; show_status()
simply never asked for them.

Confirmed live before the fix: draining a department's budget to $0 and
submitting two tasks to it escalates both via the real budget-denial path
(tasks.json: ["escalated", "escalated"]). show_status()'s own printed
output read `Total Tasks  2 / Queued  0 / Completed  0` - no third line,
no way for a human to tell the other two tasks existed at all, let alone
why. Same shape as checkpoints 72/88/90/94/95: a real, varying value
computed in one place and lost before reaching whoever is supposed to read
it - except here the correct computation was already sitting in the
sibling function, so there was no ambiguity about what to copy.

Fixed: show_status()'s `stats` dict now also counts "escalated" and
"failed" (identical list-comprehension shape to the existing two), and
prints an `Escalated` / `Failed` line for each - the exact same five keys
and counting logic build_status_payload() already uses, so the two views
can't drift apart on what a status means. No change to queued/completed's
own counting, and no change to any other show_status() section (LLM
provider, departments, budget/capacity, over-budget flag).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import json
import os
import shutil
import tempfile

import main_v2
import web_server

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


def _inject_task(task_id: str, department: str, status: str):
    """Writes a task directly into tasks.json with a given status - used
    only for the "failed" case, since forcing a real worker-thread crash
    (the only live path to "failed") would need an intentionally broken
    LLM provider; the other three statuses in this file are all produced
    live through the real CLI/executor, not injected."""
    main_v2.init_system()
    tasks = json.loads(main_v2.TASKS_FILE.read_text())
    tasks.append({
        "id": task_id, "status": status, "department": department,
        "description": "a task", "created_at": "now", "completed_at": "now",
        "result": {"error": "simulated worker crash"} if status == "failed" else {},
    })
    main_v2.TASKS_FILE.write_text(json.dumps(tasks, indent=2))


def _status_lines(output: str) -> dict:
    """Parses show_status()'s 'Label<spaces>N' lines into {label: int}."""
    counts = {}
    for label in ("Total Tasks", "Queued", "Completed", "Escalated", "Failed"):
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith(label):
                counts[label] = int(stripped[len(label):].strip())
    return counts


def test_show_status_counts_real_escalated_tasks():
    print_section("1. show_status() Counts Real, Live-Escalated Tasks")

    with IsolatedCwd():
        # Same drain-the-budget idiom as test_task_escalation_visibility.py:
        # forces two tasks through the real budget-denial escalation path,
        # not an injected status.
        main_v2.budget_manager.allocate("status_drought", 100)
        main_v2.budget_manager.get_budget("status_drought").spent = 100

        main_v2.submit_task("will be escalated A", department="status_drought", estimated_hours=1.0)
        main_v2.submit_task("will be escalated B", department="status_drought", estimated_hours=1.0)
        main_v2.process_tasks(parallel=False)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        print(f"  real statuses on disk: {[t['status'] for t in tasks]}")
        assert tasks[0]["status"] == "escalated" and tasks[1]["status"] == "escalated"

        _, output = _captured(main_v2.show_status)
        print(output)
        counts = _status_lines(output)
        print(f"  parsed counts: {counts}")

        assert counts["Total Tasks"] == 2
        assert counts["Queued"] == 0
        assert counts["Completed"] == 0
        assert counts["Escalated"] == 2, "the bug: this used to be entirely absent from the output"
        assert counts["Failed"] == 0
        # The defect's own symptom: total must reconcile with the other four.
        assert counts["Total Tasks"] == (
            counts["Queued"] + counts["Completed"] + counts["Escalated"] + counts["Failed"]
        )


def test_show_status_counts_failed_tasks():
    print_section("2. show_status() Counts Failed Tasks")

    with IsolatedCwd():
        main_v2.submit_task("a real completed task", department="eng_status_mix", estimated_hours=1.0)
        main_v2.process_tasks(parallel=False)
        _inject_task("9001", "eng_status_mix", "failed")

        _, output = _captured(main_v2.show_status)
        print(output)
        counts = _status_lines(output)
        print(f"  parsed counts: {counts}")

        assert counts["Total Tasks"] == 2
        assert counts["Completed"] == 1
        assert counts["Failed"] == 1, "the bug: this used to be entirely absent from the output"
        assert counts["Escalated"] == 0
        assert counts["Queued"] == 0


def test_show_status_matches_build_status_payload_for_a_full_status_mix():
    """Pins that main_v2.show_status()'s printed counts and web_server.py's
    build_status_payload() - which its own docstring says mirrors
    show_status() - agree exactly, for one task of each of the four
    statuses plus a still-queued one. This is the actual invariant the fix
    restores: the two views can no longer silently drift on what a status
    means, because show_status() now computes its counts the same way
    build_status_payload() always has."""
    print_section("3. show_status() And build_status_payload() Agree On Every Status")

    with IsolatedCwd():
        main_v2.budget_manager.allocate("status_mix_drought", 100)
        main_v2.budget_manager.get_budget("status_mix_drought").spent = 100
        main_v2.submit_task("will escalate", department="status_mix_drought", estimated_hours=1.0)

        main_v2.submit_task("will complete", department="eng_status_mix_2", estimated_hours=1.0)

        main_v2.process_tasks(parallel=False)  # completes task 2, escalates task 1

        _inject_task("9002", "eng_status_mix_2", "failed")
        main_v2.submit_task("stays queued", department="eng_status_mix_2", estimated_hours=1.0)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        print(f"  real statuses on disk: {sorted(t['status'] for t in tasks)}")
        assert sorted(t["status"] for t in tasks) == ["completed", "escalated", "failed", "queued"]

        _, output = _captured(main_v2.show_status)
        cli_counts = _status_lines(output)
        payload = web_server.build_status_payload()["tasks"]
        print(f"  CLI counts:     {cli_counts}")
        print(f"  payload counts: {payload}")

        assert cli_counts["Total Tasks"] == payload["total"] == 4
        assert cli_counts["Queued"] == payload["queued"] == 1
        assert cli_counts["Completed"] == payload["completed"] == 1
        assert cli_counts["Escalated"] == payload["escalated"] == 1
        assert cli_counts["Failed"] == payload["failed"] == 1


def main():
    print("\n" + "=" * 60)
    print("  show_status() ESCALATED/FAILED COUNT VISIBILITY TESTS")
    print("=" * 60)

    test_show_status_counts_real_escalated_tasks()
    test_show_status_counts_failed_tasks()
    test_show_status_matches_build_status_payload_for_a_full_status_mix()

    print("\n" + "=" * 60)
    print("  [OK] All show_status() escalated/failed count tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
