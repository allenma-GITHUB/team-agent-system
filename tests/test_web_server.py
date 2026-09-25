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


def test_task_detail_endpoint_exposes_the_fields_the_dashboard_needs():
    """checkpoint 46's own Next Steps named this gap explicitly: the per-task
    fields TaskExecutor.execute() stopped discarding (tokens_used, token_cost,
    quality_score, used_tools/tool_calls, llm_provider, execution_steps) were
    real in TASKS_FILE and in main_v2.show_task()'s printed output, but the
    dashboard's own task list never fetched /api/tasks/<id> at all - so the
    same real numbers that reached the CLI stayed invisible in the browser.
    This pins the data contract the dashboard's new detail view (added this
    checkpoint) reads from: a completed task's full record via GET
    /api/tasks/<id> carries every field renderTaskDetail() in dashboard.html
    branches on, with genuine non-placeholder values."""
    print_section("9. /api/tasks/<id> Carries The Fields The Detail View Reads")

    with RunningServer() as server:
        status, created = server.post("/api/submit", {
            "description": "Investigate a checkout error", "department": "engineering",
        })
        task_id = created["task"]["id"]
        server.post("/api/process", {})

        status, task = server.get(f"/api/tasks/{task_id}")
        print(f"  result keys: {sorted(task['result'].keys())}")
        assert status == 200
        assert task["status"] == "completed"
        result = task["result"]
        assert result["llm_provider"] == "mock"
        assert result["tokens_used"] > 0
        assert result["token_cost"] > 0
        assert result["quality_score"] > 0
        assert result["used_tools"] is True
        assert result["tool_calls"] >= 1
        assert result["execution_steps"], "a completed task should list staff contributions"

        # The task list view intentionally strips `result` (see
        # _task_summary) - the detail fields must come from the per-task
        # endpoint, not leak into the list one. It does surface two scalars
        # (quality_score, token_cost) at the top level, so the Tasks table
        # can show a per-row signal without a click-through for every task.
        status, tasks = server.get("/api/tasks")
        assert "result" not in tasks[0]
        assert tasks[0]["quality_score"] == result["quality_score"]
        assert tasks[0]["token_cost"] == result["token_cost"]


def test_escalated_task_detail_has_no_execution_only_fields():
    """The other half of the same contract: main_v2.show_task() only prints
    llm_provider/tokens_used/quality_score/etc. when result.get(...) finds
    them, because an escalated-before-execution task never ran far enough to
    compute any of them. The dashboard's detail view relies on the same
    absence (not a zeroed-out placeholder) to know a task never executed -
    confirmed live here, not assumed."""
    print_section("10. An Escalated Task's Detail Has No Placeholder Execution Fields")

    with RunningServer() as server:
        server.post("/api/submit", {
            "description": "Negotiate a new sales contract pricing strategy",
            "department": "support",
        })
        server.post("/api/process", {})

        status, tasks = server.get("/api/tasks")
        task_id = tasks[0]["id"]
        status, task = server.get(f"/api/tasks/{task_id}")
        print(f"  status={task['status']} result keys: {sorted(task['result'].keys())}")
        assert status == 200
        assert task["status"] == "escalated"
        result = task["result"]
        for absent in ("llm_provider", "tokens_used", "quality_score", "used_tools"):
            assert absent not in result, f"{absent} should never have been computed for this task"

        # Same absence, in the list view's pulled-up scalars: an escalated
        # task never gets a quality_score/token_cost badge in the Tasks
        # table, since neither was ever computed - not a "—" masquerading
        # as a real zero.
        status, tasks = server.get("/api/tasks")
        assert "quality_score" not in tasks[0]
        assert "token_cost" not in tasks[0]


def test_dashboard_html_wires_up_the_task_detail_view_safely():
    """Pins the frontend wiring added alongside the above: a task row click
    opens a detail panel populated from GET /api/tasks/<id>, and every
    dynamic field (task description, department, analysis, staff
    contributions, ...) goes through escapeHtml() before landing in
    innerHTML - a task's description is user-submitted text that reaches
    this page verbatim, so rendering it unescaped would be a stored-XSS
    hole, not just a cosmetic bug. Regression coverage for both existing
    (t.description) and newly-added interpolations, so a future edit can't
    quietly drop the escaping again."""
    print_section("11. Dashboard HTML Wires The Detail View And Escapes User Text")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="task-detail"' in body
        assert 'id="task-detail-body"' in body
        assert "function openTaskDetail" in body
        assert "function escapeHtml" in body
        assert "/api/tasks/" in body

        # The task list row must escape the description it renders, not
        # interpolate it raw - `desc` is the (possibly truncated) escaped
        # value; the raw `t.description` must never be interpolated directly.
        assert "escapeHtml(desc)" in body
        assert "${t.description}" not in body

        # Every field pulled from a completed task's result and shown in the
        # detail view must be escaped too, not just the top-level task info.
        for expr in (
            "escapeHtml(task.description)", "escapeHtml(task.department)",
            "escapeHtml(result.summary", "escapeHtml(result.analysis",
            "escapeHtml(result.llm_provider)", "escapeHtml(s.role",
            "escapeHtml(s.contribution",
        ):
            assert expr in body, f"expected {expr!r} in dashboard.html - an unescaped interpolation regressed"

        # The Tasks list row now carries a compact per-row quality/cost
        # signal (t.quality_score/t.token_cost, pulled up by
        # web_server._task_summary) instead of requiring a click-through
        # for every row just to see whether a task was cheap or expensive.
        assert "resultBadge" in body
        assert "t.quality_score" in body
        assert "t.token_cost" in body

        # The Status/Dept/Result headers are clickable sort controls, not
        # plain <th> text - checkpoint 48's own Next Steps named "a sortable
        # Tasks table" as the one item left that wasn't a policy question
        # for the owner. Sorting by quality_score is what "sort by Result"
        # means, since that's what the Result column's badge renders.
        assert 'sortableHeader("Status", "status")' in body
        assert 'sortableHeader("Dept", "department")' in body
        assert 'sortableHeader("Result", "quality_score")' in body
        assert "function sortTasks" in body
        assert "th.sortable" in body


def test_dashboard_tasks_table_sorts_by_status_department_and_result():
    """Server-side proof that the three sort keys the dashboard's header
    clicks drive (status, department, quality_score) each produce a
    genuinely different, correctly-ordered row set from three tasks with
    distinct status/department/quality_score values - the frontend sort
    function itself is plain JS pinned by string assertions above (no
    headless browser dependency, per checkpoint 47's own reasoning), so this
    proves the /api/tasks data those three keys sort over is real and
    distinguishable, not that three rows happen to look the same already."""
    print_section("12. Tasks Table Data Supports Sorting By Status/Dept/Result")

    with RunningServer() as server:
        server.post("/api/submit", {
            "description": "Investigate a checkout error", "department": "engineering",
        })
        server.post("/api/submit", {
            "description": "Negotiate a new sales contract pricing strategy",
            "department": "support",
        })
        server.post("/api/process", {})

        status, tasks = server.get("/api/tasks")
        print(f"  statuses: {[t['status'] for t in tasks]}, depts: {[t['department'] for t in tasks]}")
        assert status == 200
        assert len(tasks) == 2

        statuses = {t["status"] for t in tasks}
        depts = {t["department"] for t in tasks}
        assert len(statuses) == 2, "expected one completed and one escalated task for a real status sort"
        assert len(depts) == 2, "expected two distinct departments for a real department sort"

        # Exactly one of the two tasks executed far enough to have a
        # quality_score; the other (escalated) has none. Sorting by Result
        # must put the real value before the missing one in both directions
        # - a "—" is never higher OR lower than a real score, it's absent.
        scored = [t for t in tasks if "quality_score" in t]
        unscored = [t for t in tasks if "quality_score" not in t]
        assert len(scored) == 1 and len(unscored) == 1


def test_dashboard_html_wires_up_the_performance_panel():
    """`/api/report`'s `system` and `top_performers` keys - the same
    cumulative agent-performance data main_v2.show_report() has printed to
    the CLI all along, via PerformanceAnalytics.generate_report() - were
    already asserted present in the JSON payload by test 8 above, but
    nothing in dashboard.html ever read either key: loadReport() only ever
    destructured report.recommendations/report.capacity_recommendations.
    The System Overview stats and the "Top Performers" list a browser could
    see were consequently always empty, the same shape of gap checkpoints
    45-49 each found and closed one layer at a time. This pins the frontend
    wiring that closes it: the two new containers exist, loadReport() reads
    the fields sys.avg_quality/sys.system_success_rate/sys.avg_turnaround_time/
    sys.total_cost and report.top_performers into them, an agent name goes
    through escapeHtml() like every other dynamic value on this page, and
    the empty state (no agent has completed a task yet) is handled
    explicitly rather than rendering "NaN" or a blank panel."""
    print_section("13. Dashboard HTML Wires Up The Performance Panel")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="perf-stat-row"' in body
        assert 'id="perf-top-list"' in body
        assert "report.system" in body
        assert "report.top_performers" in body
        assert "sys.avg_quality" in body
        assert "sys.system_success_rate" in body
        assert "sys.avg_turnaround_time" in body
        assert "sys.total_cost" in body
        assert "escapeHtml(p.name)" in body

        # Same absence-means-never-computed convention as the Result column
        # and task detail view: get_system_metrics() returns all-zero
        # defaults (not a crash) before any agent has completed a task, and
        # that must render as an explicit empty state, not "0.0/5.0".
        assert "No completed tasks with recorded metrics yet." in body
        assert "No agent performance data yet." in body


def test_report_payload_carries_bottleneck_fields_the_dashboard_now_reads():
    """Checkpoint 51: `SystemMetrics.bottleneck_department` was declared but
    never assigned by get_system_metrics() - always None in the JSON
    `system` object regardless of real data - and `bottleneck_agent`, while
    correctly computed, was never read by any dashboard code even though it
    rode along in every /api/report response via asdict(system). Confirmed
    both fixed: after real tasks land in at least two departments, both
    fields are non-null strings (not asserting *which* department/agent
    wins - analytics is a module-level singleton accumulating across every
    test in this file and every pass of the idempotency loop, same caveat
    as test 8 above), and dashboard.html's Performance panel reads them off
    the same payload."""
    print_section("14. /api/report Carries Real bottleneck_agent/bottleneck_department")

    with RunningServer() as server:
        server.post("/api/submit", {"description": "Investigate a checkout error", "department": "engineering"})
        server.post("/api/submit", {"description": "Redesign the pricing page", "department": "design"})
        server.post("/api/process", {})

        status, report = server.get("/api/report")
        sys_ = report["system"]
        print(f"  bottleneck_agent: {sys_['bottleneck_agent']}")
        print(f"  bottleneck_department: {sys_['bottleneck_department']}")
        assert status == 200
        assert sys_["bottleneck_agent"]
        assert sys_["bottleneck_department"]

        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")
        assert 'id="perf-bottleneck"' in body
        assert "sys.bottleneck_agent" in body
        assert "sys.bottleneck_department" in body


def test_report_payload_resources_reflect_real_budget_and_capacity_after_a_task_runs():
    """budget_manager/capacity_manager are module-level singletons like
    analytics - state accumulates across every test in this file and every
    pass of the idempotency loop below, so this asserts real growth (an
    allocation exists, spend increased) rather than an absolute number.
    TaskExecutor.execute() calls budget_manager.ensure_allocated() on first
    use of a department (task_executor_v2.py), so processing one real task
    is enough to move get_resource_summary()'s org-wide totals off zero."""
    print_section("15. /api/report's resources Reflect Real Budget/Capacity Data")

    with RunningServer() as server:
        before = server.get("/api/report")[1]["resources"]["budget"]["total_spent"]

        server.post("/api/submit", {"description": "Investigate a checkout error", "department": "engineering"})
        server.post("/api/process", {})

        status, report = server.get("/api/report")
        resources = report["resources"]
        print(f"  budget: {resources['budget']}")
        print(f"  capacity_utilization: {resources['capacity_utilization']}")
        assert status == 200
        assert resources["budget"]["total_allocated"] > 0
        assert resources["budget"]["total_spent"] > before
        assert 0.0 <= resources["capacity_utilization"] <= 1.0
        assert isinstance(resources["over_budget_departments"], list)


def test_dashboard_html_wires_up_the_resource_panel():
    """Checkpoint 51's own Next Steps named this gap: get_resource_summary()
    has shipped `resources` (org-wide budget totals, capacity_utilization,
    over_budget_departments) on every /api/report response since the report
    endpoint was added, and generate_report() has printed the same numbers
    as the CLI's "Resource Overview" section all along - but no dashboard
    code ever read the `resources` key; only its `capacity_recommendations`
    sibling made it into the generic Recommendations list. The Departments
    panel's per-department budget/capacity bars (from /api/status) conveyed
    a related but different signal - never the org-wide rollup. This pins
    the frontend wiring that closes it: the new panel and its containers
    exist, loadReport() reads report.resources into them, department names
    in the over-budget warning go through escapeHtml() like every other
    dynamic value on this page, and the panel stays hidden (guard mirrors
    generate_report()'s own `budget_summary["total_allocated"] > 0` check)
    until something's actually been allocated."""
    print_section("16. Dashboard HTML Wires Up The Resource Overview Panel")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="resource-panel" hidden' in body
        assert 'id="resource-stat-row"' in body
        assert 'id="resource-alert"' in body
        assert "report.resources" in body
        assert "budget.total_allocated" in body
        assert "resources.capacity_utilization" in body
        assert "resources.over_budget_departments" in body
        assert "escapeHtml" in body.split("resource-alert")[-1][:400]


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
        test_task_detail_endpoint_exposes_the_fields_the_dashboard_needs()
        test_escalated_task_detail_has_no_execution_only_fields()
        test_dashboard_html_wires_up_the_task_detail_view_safely()
        test_dashboard_tasks_table_sorts_by_status_department_and_result()
        test_dashboard_html_wires_up_the_performance_panel()
        test_report_payload_carries_bottleneck_fields_the_dashboard_now_reads()
        test_report_payload_resources_reflect_real_budget_and_capacity_after_a_task_runs()
        test_dashboard_html_wires_up_the_resource_panel()

    print("\n" + "=" * 60)
    print("  [OK] All web dashboard tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
