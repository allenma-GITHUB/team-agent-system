#!/usr/bin/env python3
"""
A completed task's result["metrics"] dict (avg_quality, tasks_completed,
error_rate - DepartmentHeadAgent.run()'s own running performance numbers,
task_executor_v2.py ~line 770) has been computed and persisted to
data/tasks.json on every completed task since checkpoint 45 wired the rest
of DepartmentHeadAgent.run()'s result through TaskExecutor.execute()
unfiltered. grep for "metrics" outside its declaration/write site and
test_executor_result_fields.py's own "was it set" assertion turns up no
reader anywhere: main_v2.show_task() prints Summary/Analysis/LLM
Provider/Tokens Used/Quality Score/Tool Calls/Delegation Chain/Staff
Contributions but silently skips the metrics dict sitting right next to
quality_score in the same result object, and static/dashboard.html mirrors
that same omission field-for-field in its JS renderer.

Confirmed live before this fix: submitting and processing a real task
produced a real, non-zero metrics dict in data/tasks.json
(avg_quality=4.0, tasks_completed=1, error_rate=0.0), but
show_task()'s captured stdout had no "Metrics" line anywhere in it.

Fixed: main_v2.show_task() and static/dashboard.html's renderTaskDetail()
each gained one line/section printing avg_quality, tasks_completed and
error_rate under a "Agent Metrics" label, right after Quality Score - same
.get()-guarded, absence-means-never-ran convention every other optional
result field in both files already follows.

Not fixed because it was already fine: web_server.py's GET /api/tasks/<id>
returns the task dict unmodified (no field allowlist), so it has carried
"metrics" to any API consumer since the day that route shipped - this test
pins that fact so a future refactor that adds a field allowlist there
doesn't quietly reintroduce the same gap this checkpoint just closed in the
two human-facing renderers.

CHECKPOINT 81 follow-up: this fix only wired 3 of PerformanceMetrics' 5
fields (agent_state.py) into the dict - avg_cost_per_task and
avg_completion_time_hours were computed by the same update() call, written
to disk, and left out of task_executor_v2.py's "metrics" dict (and so out
of both renderers) entirely. Confirmed live: data/agent_states.json carried
a real agent with avg_completion_time_hours=3.375, avg_cost_per_task=63.28
while every "metrics" dict ever returned by DepartmentHeadAgent.run() had
only the other three keys. Fixed the same way checkpoint 72 fixed the
first three: both fields added to the dict and to each renderer's line
right after error_rate.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import json
import re
import shutil
import tempfile
import threading
import urllib.request

import main_v2
import web_server
from http.server import ThreadingHTTPServer

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


def test_show_task_prints_the_real_metrics_dict():
    print_section("1. show_task() Prints The Agent Metrics It Persisted")

    with IsolatedCwd():
        task_id = main_v2.submit_task("Quick typo fix in docs", estimated_hours=1.0)
        main_v2.process_tasks(parallel=False)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        task = next(t for t in tasks if t["id"] == task_id)
        metrics = task["result"]["metrics"]
        print(f"  persisted metrics: {metrics}")

        assert task["status"] == "completed"
        assert metrics["tasks_completed"] >= 1
        assert metrics["avg_quality"] > 0
        assert metrics["avg_cost_per_task"] > 0
        assert metrics["avg_completion_time_hours"] > 0

        _, output = _captured(main_v2.show_task, task_id)
        print(output)

        assert "Agent Metrics" in output
        assert f"avg_quality={metrics['avg_quality']:.2f}" in output
        assert f"tasks_completed={metrics['tasks_completed']}" in output
        assert f"error_rate={metrics['error_rate']:.1%}" in output
        assert f"avg_cost_per_task=${metrics['avg_cost_per_task']:.5f}" in output
        assert f"avg_completion_time_hours={metrics['avg_completion_time_hours']:.2f}" in output


def test_show_task_omits_the_metrics_line_when_never_run():
    print_section("2. show_task() Omits Agent Metrics For A Queued Task")

    with IsolatedCwd():
        task_id = main_v2.submit_task("Something still queued")
        _, output = _captured(main_v2.show_task, task_id)
        print(output)

        assert "Agent Metrics" not in output


def test_dashboard_html_renders_the_same_metrics_fields():
    print_section("3. dashboard.html's JS Mirrors The Same Fix")

    dashboard = (REPO_ROOT and
                 open(os.path.join(REPO_ROOT, "static", "dashboard.html")).read())
    print(f"  dashboard.html length: {len(dashboard)} bytes")

    assert "result.metrics" in dashboard
    assert "Agent Metrics" in dashboard
    # The guard must exist before the Quality Score block's own comparable
    # guard is referenced below it, matching every other optional field in
    # this renderer (used_tools, delegation_chain, ...).
    assert re.search(r"if\s*\(result\.metrics\)\s*\{", dashboard)
    assert "m.avg_cost_per_task" in dashboard
    assert "m.avg_completion_time_hours" in dashboard


def test_api_tasks_detail_already_carried_metrics_unmodified():
    print_section("4. GET /api/tasks/<id> Already Passed Metrics Through Untouched")

    original_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    try:
        shutil.copy(os.path.join(REPO_ROOT, "config.json"), tmpdir)
        os.chdir(tmpdir)

        task_id = main_v2.submit_task("Quick typo fix in docs", estimated_hours=1.0)
        main_v2.process_tasks(parallel=False)

        server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.DashboardRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/tasks/{task_id}", timeout=10) as resp:
                status = resp.status
                body = json.loads(resp.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

        print(f"  status={status} result.metrics={body.get('result', {}).get('metrics')}")
        assert status == 200
        assert isinstance(body["result"]["metrics"], dict)
        assert body["result"]["metrics"]["tasks_completed"] >= 1
        assert body["result"]["metrics"]["avg_cost_per_task"] > 0
        assert body["result"]["metrics"]["avg_completion_time_hours"] > 0
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_show_task_prints_the_real_metrics_dict()
    test_show_task_omits_the_metrics_line_when_never_run()
    test_dashboard_html_renders_the_same_metrics_fields()
    test_api_tasks_detail_already_carried_metrics_unmodified()
    print("\n✅ All task-metrics-visibility tests passed!")
