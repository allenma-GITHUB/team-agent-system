#!/usr/bin/env python3
"""
Regression tests for web_server.py, the visual dashboard's HTTP layer.

Runs the real ThreadingHTTPServer on an OS-assigned ephemeral port (port 0)
inside an isolated temp directory (same IsolatedCwd pattern as
test_main_cli_core.py) with a copy of the real config.json, so it never
touches the actual repo's data/ files and never collides with a port some
other process on the machine already holds.

The point of these tests is the wiring, not the business logic underneath
it (that's covered elsewhere - test_main_cli_core.py, test_decision_inputs.py,
etc.): does the server route requests correctly, translate HTTP methods and
JSON bodies to and from main_v2's own submit_task()/process_tasks(), and
surface the same escalation the CLI would rather than silently reporting a
task as completed.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import shutil
import tempfile
import threading
import urllib.error
import urllib.request

import main_v2
import web_server
from http.server import ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class RunningServer:
    """Starts web_server's real HTTP server on an ephemeral port inside a
    fresh temp cwd (isolated from the repo's data/ files, like
    test_main_cli_core.py's IsolatedCwd), and tears both down on exit."""

    def __enter__(self):
        self._original_cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        shutil.copy(os.path.join(REPO_ROOT, "config.json"), self._tmpdir)
        os.chdir(self._tmpdir)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.DashboardRequestHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        os.chdir(self._original_cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def get(self, path: str):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def post(self, path: str, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


def test_root_serves_the_dashboard_html():
    print_section("1. GET / Serves The Dashboard Page")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            content_type = resp.headers.get("Content-Type")

        print(f"  status={status} content_type={content_type} bytes={len(body)}")
        assert status == 200
        assert "text/html" in content_type
        assert "<title>Team Agent System</title>" in body


def test_status_and_tasks_reflect_a_real_submit_and_process_cycle():
    print_section("2. Submit -> Process Through The API Matches The CLI's Own State")

    with RunningServer() as server:
        status, before = server.get("/api/status")
        print(f"  before: {before['tasks']}")
        assert status == 200
        assert before["tasks"]["total"] == 0

        status, created = server.post("/api/submit", {
            "description": "Fix a login bug", "department": "engineering",
        })
        print(f"  submit -> status={status} task={created.get('task')}")
        assert status == 201
        assert created["task"]["department"] == "engineering"
        assert created["task"]["status"] == "queued"
        assert "Task" in created["log"]  # main_v2.submit_task()'s own print, captured

        status, processed = server.post("/api/process", {})
        print(f"  process -> status={status} tasks={processed['tasks']}")
        assert status == 200
        assert processed["tasks"][0]["status"] == "completed"
        assert "Processing 1 task(s)" in processed["log"]

        status, after = server.get("/api/status")
        assert after["tasks"]["total"] == 1
        assert after["tasks"]["completed"] == 1

        # Cross-check against main_v2's own on-disk state directly - the API
        # and the CLI must be reading and writing the exact same file, not
        # two copies that could drift.
        tasks_on_disk = json.loads(main_v2.TASKS_FILE.read_text())
        assert len(tasks_on_disk) == 1
        assert tasks_on_disk[0]["status"] == "completed"


def test_a_genuine_skill_mismatch_escalates_through_the_api_too():
    """The same live behavior demonstrated via the CLI in checkpoint 38:
    a task requiring skills a low-skill department head doesn't have must
    come back "escalated", not "completed" - through this HTTP layer too,
    not just when called directly in Python.

    Deliberately sales-only vocabulary (no "architecture"): budget_manager,
    capacity_manager and agent_registry are module-level singletons main_v2
    imports directly, not dependency-injected per RunningServer like
    task_executor_v2's own tests - so a fresh temp cwd here does NOT reset
    which agents are already registered in memory from earlier tests in
    this same process (e.g. engineering_head, from test 2 above). If this
    task's inferred skills overlapped engineering's real expertise
    ("architecture"), find_best_delegate() could legitimately route it to
    the already-registered engineering_head instead of escalating - a
    order-dependent flake, not a bug in the thing under test. Sales
    vocabulary has no registered department head anywhere in this file to
    delegate to, so escalation is deterministic regardless of test order
    or how many times the idempotency loop below has already run."""
    print_section("3. A Genuine Escalation Surfaces Through The API")

    with RunningServer() as server:
        server.post("/api/submit", {
            "description": "Negotiate a new sales contract pricing strategy",
            "department": "support",
        })
        status, processed = server.post("/api/process", {})
        summary_line = next((l for l in processed["log"].splitlines() if "escalated" in l), "")
        print(f"  {processed['tasks'][0]['status']}: {summary_line}")
        assert status == 200
        assert processed["tasks"][0]["status"] == "escalated"
        assert "Missing expertise" in processed["log"]


def test_submit_rejects_negative_hours_with_the_real_reason():
    print_section("4. A Rejected Submit Surfaces submit_task()'s Own Reason")

    with RunningServer() as server:
        status, body = server.post("/api/submit", {"description": "bad task", "estimated_hours": -5})
        print(f"  status={status} body={body}")
        assert status == 400
        assert "estimated_hours must be >= 0" in body["error"]

        status, tasks = server.get("/api/tasks")
        assert tasks == [], "a rejected submit must not create a task"


def test_submit_requires_a_description():
    print_section("5. Submit Requires A Non-Empty Description")

    with RunningServer() as server:
        status, body = server.post("/api/submit", {"description": "   "})
        print(f"  status={status} body={body}")
        assert status == 400
        assert "description" in body["error"]


def test_malformed_json_body_is_a_clean_400_not_a_crash():
    print_section("6. Malformed JSON Body Is A Clean 400")

    with RunningServer() as server:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/api/submit", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raised = False
        except urllib.error.HTTPError as e:
            raised = True
            status = e.code
            body = json.loads(e.read())
        print(f"  status={status if raised else 'N/A'} body={body if raised else None}")
        assert raised and status == 400
        assert "malformed" in body["error"]


def test_unknown_routes_and_missing_tasks_are_404_not_500():
    print_section("7. Unknown Routes And Missing Tasks Are 404")

    with RunningServer() as server:
        status, body = server.get("/api/nope")
        print(f"  unknown route -> status={status} body={body}")
        assert status == 404

        status, body = server.get("/api/tasks/9999")
        print(f"  missing task -> status={status} body={body}")
        assert status == 404


def test_report_payload_has_the_shape_the_dashboard_reads():
    """analytics (PerformanceAnalytics) is a module-level singleton like
    budget_manager/capacity_manager/agent_registry - its total_tasks
    accumulates across every test in this file and every pass of the
    idempotency loop below, the same caveat CLAUDE.md documents for
    AgentRegistry.register(). So this asserts the count grew by exactly
    one task, not that it equals one absolutely."""
    print_section("8. /api/report Has The Fields The Frontend Reads")

    with RunningServer() as server:
        before_total = server.get("/api/report")[1]["system"]["total_tasks"]

        server.post("/api/submit", {"description": "Investigate a checkout error", "department": "engineering"})
        server.post("/api/process", {})

        status, report = server.get("/api/report")
        print(f"  keys: {sorted(report.keys())}")
        assert status == 200
        for key in ("system", "top_performers", "recommendations", "resources", "capacity_recommendations"):
            assert key in report
        assert report["system"]["total_tasks"] == before_total + 1


def main():
    print("\n" + "=" * 60)
    print("  WEB DASHBOARD (web_server.py) REGRESSION TESTS")
    print("=" * 60)

    for _ in range(2):  # idempotent: each test runs in a fresh temp cwd
        test_root_serves_the_dashboard_html()
        test_status_and_tasks_reflect_a_real_submit_and_process_cycle()
        test_a_genuine_skill_mismatch_escalates_through_the_api_too()
        test_submit_rejects_negative_hours_with_the_real_reason()
        test_submit_requires_a_description()
        test_malformed_json_body_is_a_clean_400_not_a_crash()
        test_unknown_routes_and_missing_tasks_are_404_not_500()
        test_report_payload_has_the_shape_the_dashboard_reads()

    print("\n" + "=" * 60)
    print("  [OK] All web dashboard tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
