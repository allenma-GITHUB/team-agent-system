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
from agent_state import agent_registry, AgentProfile
from budgets import budget_manager
from http.server import ThreadingHTTPServer
from workflows import workflow_engine

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


def test_report_payload_resources_include_expense_breakdown_after_a_task_runs():
    """Checkpoint 68: get_resource_summary() has always shipped `resources`
    in /api/report (the test above pins `budget`/`capacity_utilization`),
    but nothing summed a department's spend by category - `expense_log`
    (budgets.py) logged a real "labor" ExpenseRecord on every task's
    budget check from the start, with zero readers anywhere outside
    budgets.py's own persistence and tests. expense_breakdown() finally
    sums it; this proves the new key reaches the same endpoint the
    existing resources test above already checks, with a real nonzero
    "labor" total after one real task runs."""
    print_section("15b. /api/report's resources Carry A Real expense_breakdown")

    with RunningServer() as server:
        server.post("/api/submit", {"description": "Investigate a checkout error", "department": "engineering"})
        server.post("/api/process", {})

        status, report = server.get("/api/report")
        breakdown = report["resources"]["expense_breakdown"]
        print(f"  expense_breakdown: {breakdown}")
        assert status == 200
        assert "labor" in breakdown
        assert breakdown["labor"] > 0


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

        # expense_breakdown (checkpoint 68) rides the same payload and the
        # same panel - reads resources.expense_breakdown, escapes the
        # category name like every other dynamic string here, and uses the
        # 5-decimal money formatter for a sub-$1 category total instead of
        # rounding it down to a misleading "$0.00".
        assert 'id="resource-breakdown"' in body
        assert "resources.expense_breakdown" in body
        assert "escapeHtml(c)" in body
        assert "moneyPrecise(breakdown[c])" in body


def test_strategy_endpoint_exposes_a_real_reallocation_proposal():
    """strategy.py's StrategicPlanner.propose_reallocations() has computed
    real donor-to-recipient budget moves since it was built - main_v2.py's
    `strategy` command has printed them all along - but had zero web
    consumers, the same gap shape checkpoint 56's predecessor closed for
    workflows.py (build_workflows_payload()): real logic, one CLI consumer,
    nothing over the wire. Drives budget_manager (the same module-level
    singleton test 15 above already documents as accumulating across every
    test and idempotency pass in this file) to a genuine skew - one
    department pinned at 95% utilization, another freshly allocated and
    untouched at 0% - via allocate()/request_expense() exactly as a real
    task's budget charge would, then confirms /api/strategy reports the
    same proposal propose_reallocations() itself would compute, not a
    placeholder shape. allocate() resets rather than accumulates (see its
    own docstring), so the two departments under test are deterministic -
    but propose_reallocations() funds a recipient from its *largest* idle
    donor first (greedy by giveable amount), and other departments in this
    shared singleton may also be sitting idle with their own config.json-
    seeded allocation from earlier tests. "product" is given an allocation
    no other department's idle budget could outrank, so it - not whichever
    department happened to be touched last elsewhere in the suite - is
    deterministically the donor named here. "research"/"product" are used
    nowhere else in this file as a real submit target, unlike engineering/
    support/design - so pinning them here can't starve a later test's own
    department of the budget it needs to actually complete a task."""
    print_section("17. GET /api/strategy Reports A Real Reallocation Proposal")

    with RunningServer() as server:
        budget_manager.allocate("research", 10000)
        budget_manager.request_expense("research", 9500, "labor")
        budget_manager.allocate("product", 10_000_000)

        status, proposals = server.get("/api/strategy")
        print(f"  proposals: {proposals}")
        assert status == 200
        proposal = next((p for p in proposals
                          if p["from_department"] == "product" and p["to_department"] == "research"), None)
        assert proposal is not None, "expected product (idle) -> research (95% utilized) in /api/strategy"
        assert proposal["amount"] > 0
        assert "product" in proposal["reason"] and "research" in proposal["reason"]


def test_dashboard_html_wires_up_the_strategy_panel():
    """Pins the frontend wiring that closes the gap the test above proves at
    the API layer: the panel and its containers exist, loadStrategy() reads
    /api/strategy into them, every dynamic string (from_department,
    to_department, reason) goes through escapeHtml() like every other value
    on this page, and the panel stays hidden until a proposal actually
    exists - this is a preview only, never a dashboard button that applies
    the move (that stays `strategy --apply` on the CLI, a reviewed action
    per this project's "safety boundaries enforced in code" rule)."""
    print_section("18. Dashboard HTML Wires Up The Strategic Reallocation Panel")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="strategy-panel" hidden' in body
        assert 'id="strategy-list"' in body
        assert "/api/strategy" in body

        script_section = body.split("async function loadStrategy")[-1].split("async function refreshAll")[0]
        assert "p.from_department" in script_section
        assert "p.to_department" in script_section
        assert "p.amount" in script_section
        assert "p.reason" in script_section
        assert "escapeHtml(p.from_department)" in script_section
        assert "escapeHtml(p.to_department)" in script_section
        assert "escapeHtml(p.reason)" in script_section

        # Never an apply/action control - this panel is a read-only mirror
        # of the CLI's default (no --apply) preview.
        assert "/api/strategy/apply" not in body


def test_budget_rollovers_endpoint_exposes_a_real_recorded_rollover():
    """BudgetManager.rollover_log has recorded a real PeriodRollover every
    time _roll_if_due() actually fires since budgets.py's rollover fix
    shipped, but had zero web consumers - the same "real logic, no wire
    consumer" gap shape test 17 above closed for strategy.py. Backdates a
    daily budget's period_start (same technique test_budgets.py's own
    rollover tests use) and drives a real rollover via get_budget(), then
    confirms /api/budget-rollovers reports it, scoped by ?department= and
    org-wide. Uses "legal" - unused as a budget_manager department anywhere
    else in this shared-singleton file, so a second rollover from a prior
    idempotency pass (this file's main() runs every test twice) only ever
    adds a *newer* "legal" entry rather than changing what the most recent
    one says."""
    print_section("19. GET /api/budget-rollovers Reports A Real Rollover")

    with RunningServer() as server:
        from datetime import datetime, timedelta

        budget = budget_manager.allocate("legal", 4000, period="daily")
        budget_manager.request_expense("legal", 3000, "labor")
        budget.period_start = (datetime.utcnow() - timedelta(days=2)).isoformat()
        budget_manager.get_budget("legal")  # drives the overdue rollover

        status, rollovers = server.get("/api/budget-rollovers?department=legal")
        print(f"  legal-only rollovers: {rollovers}")
        assert status == 200
        assert len(rollovers) >= 1
        newest = rollovers[0]
        assert newest["department"] == "legal"
        assert newest["spent_in_period"] == 3000
        assert newest["unused_forfeited"] == 1000  # 4000 allocated - 3000 spent

        status, all_rollovers = server.get("/api/budget-rollovers")
        print(f"  org-wide rollover count: {len(all_rollovers)}")
        assert status == 200
        assert any(r["department"] == "legal" for r in all_rollovers)


def test_dashboard_html_wires_up_the_budget_rollover_panel():
    """Pins the frontend wiring that closes the gap the test above proves at
    the API layer: the panel/list containers exist, loadBudgetRollovers()
    reads /api/budget-rollovers into them, every dynamic string goes through
    escapeHtml() like every other value on this page, and the panel stays
    hidden until a rollover has actually happened - mirrors
    test_dashboard_html_wires_up_the_strategy_panel's shape exactly."""
    print_section("20. Dashboard HTML Wires Up The Budget Rollover Panel")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="rollover-panel" hidden' in body
        assert 'id="rollover-list"' in body
        assert "/api/budget-rollovers" in body

        script_section = body.split("async function loadBudgetRollovers")[-1].split("async function refreshAll")[0]
        assert "r.department" in script_section
        assert "r.period" in script_section
        assert "r.rolled_at" in script_section
        assert "r.spent_in_period" in script_section
        assert "r.unused_forfeited" in script_section
        assert "r.period_start" in script_section
        assert "escapeHtml(r.department)" in script_section

        assert "loadBudgetRollovers()" in body.split("async function refreshAll")[-1].split("}")[0]


def test_workflows_endpoint_exposes_a_real_running_instance():
    """workflows.py's WorkflowEngine has run multi-step business processes
    (feature requests, bug fixes) since it was built, but had zero consumers
    outside main_v2.py's CLI - no API route ever shipped an instance's state,
    so no dashboard code could read it. Worse, even the CLI's own `workflow
    status <id>` command requires already knowing an instance_id (printed
    once, at `workflow start` time) - there has never been a way to list
    running instances at all. This starts a real instance through the same
    global workflow_engine/main_v2.init_workflows() the CLI's own `workflow
    start`/`workflow next` commands use, advances one step, and confirms
    /api/workflows reports exactly what get_status() (the CLI's own status
    source) computes - not a placeholder shape.

    workflow_engine is the same module-level singleton every test in this
    file's two idempotency passes share (same caveat
    test_report_payload_resources_reflect_real_budget_and_capacity_after_a_task_runs
    already documents for budget_manager/capacity_manager), so this looks up
    this pass's own instance by id rather than assuming it's the only one
    the endpoint returns."""
    print_section("21. GET /api/workflows Reports A Real Instance's Live State")

    with RunningServer() as server:
        main_v2.init_workflows()
        instance = workflow_engine.create_instance("feature_request", {"title": "Dark mode"})
        workflow_engine.start_instance(instance.instance_id)
        step = workflow_engine.get_next_step(instance.instance_id)
        workflow_engine.complete_step(instance.instance_id, step.step_id, {"summary": "done"})

        status, workflows = server.get("/api/workflows")
        print(f"  workflows returned: {len(workflows)}")
        assert status == 200
        wf = next((w for w in workflows if w["instance_id"] == instance.instance_id), None)
        assert wf is not None, f"instance {instance.instance_id} missing from /api/workflows"
        print(f"  instance {wf['instance_id']}: status={wf['status']} progress={wf['progress']:.2f} "
              f"current_step={wf['current_step']}")
        assert wf["workflow_name"] == "Feature Request Process"
        assert wf["status"] == "in_progress"
        assert wf["step_statuses"]["intake"] == "completed"
        assert wf["step_statuses"]["design"] == "pending"
        assert 0 < wf["progress"] < 1
        assert wf["error"] is None


def test_dashboard_html_wires_up_the_workflow_panel():
    """Pins the frontend wiring that closes the gap
    test_workflows_endpoint_exposes_a_real_running_instance proves at the API
    layer: the panel and its containers exist, loadWorkflows() reads
    /api/workflows into them, every dynamic string (workflow_name,
    instance_id, current_step, step id/status, error) goes through
    escapeHtml() like every other value on this page.

    The panel itself is no longer hidden until an instance exists (see
    checkpoint that added the Start/Next/Complete/Approve/Retry controls
    below) - a human needs to see the "start a workflow" form before any
    instance exists, not just after one shows up. Only the now-separate
    #workflow-empty placeholder is conditionally hidden instead."""
    print_section("22. Dashboard HTML Wires Up The Workflow Panel")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="workflow-panel"' in body
        assert 'id="workflow-panel" hidden' not in body
        assert 'id="workflow-empty"' in body
        assert 'id="workflow-list"' in body
        assert "/api/workflows" in body

        script_section = body.split("async function loadWorkflows")[-1].split("async function refreshAll")[0]
        assert "w.workflow_name" in script_section
        assert "w.step_statuses" in script_section
        assert "w.progress" in script_section
        assert "w.current_step" in script_section
        assert "escapeHtml(w.workflow_name)" in script_section
        assert "escapeHtml(w.instance_id)" in script_section
        assert "escapeHtml(stepId)" in script_section
        assert "escapeHtml(w.error)" in script_section


def test_workflow_templates_endpoint_lists_the_real_registered_templates():
    """main_v2.py's `workflow start <template_id>` has always required
    already knowing a valid template_id - `workflow list` prints the two
    real ones (feature_request, bug_fix), but had zero web consumers, the
    same gap shape /api/workflows itself had before checkpoint 19/20 above.
    Confirms the route reflects main_v2.WORKFLOW_TEMPLATES itself, not a
    hand-typed copy that could drift from it."""
    print_section("23. GET /api/workflow-templates Lists The Real Templates")

    with RunningServer() as server:
        status, templates = server.get("/api/workflow-templates")
        print(f"  status={status} templates={templates}")
        assert status == 200
        ids = {t["template_id"] for t in templates}
        assert ids == set(main_v2.WORKFLOW_TEMPLATES.keys())
        feature = next(t for t in templates if t["template_id"] == "feature_request")
        assert feature["name"] == "Feature Request Process"
        assert feature["steps"] == 7


def test_workflow_start_endpoint_creates_a_real_instance():
    """main_v2.py's `workflow start/next/complete/approve/retry` commands -
    the only way to actually drive a workflow instance forward - had zero
    web routes even after /api/workflows gave the dashboard read-only
    visibility: a human watching a stuck instance had no way to unstick it
    without the CLI. Confirms POST /api/workflow/start calls the real
    workflow_start(), returning the real instance_id (not reconstructed by
    scraping its own printed log) and a status that matches
    WorkflowEngine.get_status() exactly, and that the new instance is
    immediately visible through the pre-existing /api/workflows route."""
    print_section("24. POST /api/workflow/start Creates A Real Instance")

    with RunningServer() as server:
        status, body = server.post("/api/workflow/start", {
            "template_id": "bug_fix", "input": {"severity": "high"},
        })
        print(f"  status={status} instance={body.get('instance', {}).get('instance_id')}")
        assert status == 201
        instance_id = body["instance"]["instance_id"]
        assert body["instance"]["workflow_name"] == "Bug Fix Workflow"
        assert body["instance"]["status"] == "in_progress"
        assert "Started 'Bug Fix Workflow'" in body["log"]
        assert instance_id in body["log"]

        status, workflows = server.get("/api/workflows")
        assert status == 200
        assert any(w["instance_id"] == instance_id for w in workflows)


def test_workflow_start_endpoint_validates_input():
    """Mirrors workflow_start()'s own validation - an unknown template_id
    gets the CLI's exact "Unknown workflow template" + available-list
    message back as the error, not a generic 500 or a silently-created
    instance for a template that doesn't exist."""
    print_section("25. POST /api/workflow/start Validates Its Input")

    with RunningServer() as server:
        status, body = server.post("/api/workflow/start", {})
        print(f"  missing template_id -> status={status} body={body}")
        assert status == 400
        assert "template_id" in body["error"]

        status, body = server.post("/api/workflow/start", {"template_id": "no_such_template"})
        print(f"  unknown template_id -> status={status} body={body}")
        assert status == 400
        assert "Unknown workflow template: no_such_template" in body["error"]
        assert "feature_request" in body["error"] and "bug_fix" in body["error"]


def test_workflow_next_endpoint_advances_a_real_instance():
    """POST /api/workflow/next calls the real workflow_next() - the next
    step's name, owning department, and step_id come back in the log
    exactly as the CLI would print them, and the instance status returned
    alongside it reflects the real advance (current_step set, that step's
    own status flipped to in_progress) rather than a canned response."""
    print_section("26. POST /api/workflow/next Advances A Real Instance")

    with RunningServer() as server:
        _, started = server.post("/api/workflow/start", {"template_id": "bug_fix"})
        instance_id = started["instance"]["instance_id"]

        status, body = server.post("/api/workflow/next", {"instance_id": instance_id})
        print(f"  status={status} log={body.get('log')!r}")
        assert status == 200
        assert "Next step: Bug Triage (support)" in body["log"]
        assert "step_id: triage" in body["log"]
        assert body["instance"]["current_step"] == "triage"
        assert body["instance"]["step_statuses"]["triage"] == "in_progress"

        status, body = server.post("/api/workflow/next", {"instance_id": "no_such_instance"})
        print(f"  unknown instance -> status={status} body={body}")
        assert status == 404
        assert "no_such_instance" in body["error"]


def test_workflow_complete_endpoint_executes_a_real_step():
    """POST /api/workflow/complete calls the real workflow_complete(), which
    runs the step through the actual TaskExecutor/department pipeline (real
    budget/capacity/decision-engine checks apply, per this project's "no
    hand-typed placeholder result" rule for workflow steps) - not a faked
    success. The support department actually executes the real "triage"
    step and the instance's step_statuses reflects that, matching what
    `python main_v2.py workflow complete <id> triage` would do."""
    print_section("27. POST /api/workflow/complete Executes A Real Step")

    with RunningServer() as server:
        _, started = server.post("/api/workflow/start", {"template_id": "bug_fix"})
        instance_id = started["instance"]["instance_id"]
        server.post("/api/workflow/next", {"instance_id": instance_id})

        status, body = server.post("/api/workflow/complete", {
            "instance_id": instance_id, "step_id": "triage",
        })
        print(f"  status={status} log={body.get('log')!r}")
        assert status == 200
        assert "Step 'triage' executed and marked complete" in body["log"]
        assert "Support team completed task" in body["log"]
        assert body["instance"]["step_statuses"]["triage"] == "completed"
        assert body["instance"]["progress"] == 0.25

        status, body = server.post("/api/workflow/complete", {"instance_id": instance_id})
        print(f"  missing step_id -> status={status} body={body}")
        assert status == 400
        assert "step_id" in body["error"]


def test_workflow_approve_endpoint_enforces_the_real_approval_role():
    """POST /api/workflow/approve calls the real approve_step(), which
    checks the approver against the step's declared approval_role (see
    WorkflowEngine._approver_qualifies) instead of trusting any string a
    caller sends. An unregistered/unqualified approver_id is rejected with
    the engine's own reason; once a real agent holding that exact role is
    registered, the same approval through the same route actually commits
    (the step moves to APPROVED, and its $500 estimated_cost becomes real
    spend on the owning department's budget, not just a status flip)."""
    print_section("28. POST /api/workflow/approve Enforces The Real Role")

    with RunningServer() as server:
        _, started = server.post("/api/workflow/start", {"template_id": "bug_fix"})
        instance_id = started["instance"]["instance_id"]
        server.post("/api/workflow/next", {"instance_id": instance_id})
        server.post("/api/workflow/complete", {"instance_id": instance_id, "step_id": "triage"})
        server.post("/api/workflow/next", {"instance_id": instance_id})
        server.post("/api/workflow/complete", {"instance_id": instance_id, "step_id": "fix"})
        server.post("/api/workflow/next", {"instance_id": instance_id})  # -> verify, needs approval
        _, completed = server.post("/api/workflow/complete", {"instance_id": instance_id, "step_id": "verify"})
        assert completed["instance"]["step_statuses"]["verify"] == "awaiting_approval"

        status, body = server.post("/api/workflow/approve", {
            "instance_id": instance_id, "step_id": "verify", "approver_id": "random_nobody",
        })
        print(f"  unqualified approver -> status={status} log={body.get('log')!r}")
        assert status == 200
        assert "does not hold the required approval role" in body["log"]
        assert body["instance"]["step_statuses"]["verify"] == "awaiting_approval"

        # A real tech_lead - registered the same way test_workflow_approval_role.py
        # proves approve_step() itself requires - actually clears the gate.
        if agent_registry.get("tech_lead") is None:
            agent_registry.register(AgentProfile(
                agent_id="tech_lead", name="tech_lead", agent_type="SpecialistAgent",
                department="engineering", expertise_areas=[], skill_level=3,
                capabilities=[], constraints=[],
            ))
        status, body = server.post("/api/workflow/approve", {
            "instance_id": instance_id, "step_id": "verify", "approver_id": "tech_lead",
        })
        print(f"  qualified approver -> status={status} log={body.get('log')!r}")
        assert status == 200
        assert "approved by tech_lead" in body["log"]
        assert body["instance"]["step_statuses"]["verify"] == "approved"


def test_workflow_retry_endpoint_reports_a_real_non_blocked_state():
    """POST /api/workflow/retry calls the real retry_blocked_step() -
    proven here on the deterministic, always-reachable negative path (an
    instance with nothing BLOCKED gets the engine's own "No blocked step to
    retry" reason back, not a generic failure or a false "resumed")."""
    print_section("29. POST /api/workflow/retry Reports A Real State")

    with RunningServer() as server:
        _, started = server.post("/api/workflow/start", {"template_id": "bug_fix"})
        instance_id = started["instance"]["instance_id"]

        status, body = server.post("/api/workflow/retry", {"instance_id": instance_id})
        print(f"  status={status} log={body.get('log')!r}")
        assert status == 200
        assert "Still blocked: No blocked step to retry" in body["log"]

        status, body = server.post("/api/workflow/retry", {"instance_id": "no_such_instance"})
        print(f"  unknown instance -> status={status} body={body}")
        assert status == 404


def test_dashboard_html_wires_up_the_workflow_action_controls():
    """Pins the frontend wiring the six tests above prove at the API layer:
    the Start-a-workflow form (template select populated from
    /api/workflow-templates, free-text key=value input, Start button) and
    each instance card's Next/Complete/Approve/Reject/Retry controls post
    to the right routes, and the per-card result line survives
    loadWorkflows()'s own re-render (it rebuilds #workflow-list's innerHTML
    from scratch on every refresh, including the one each action triggers
    right after it runs) by keeping it in the wfLogs cache keyed by
    instance_id rather than only in a DOM node that render is about to
    replace - the exact bug proven live while building this feature: the
    log line was visible for a moment and then vanished the instant the
    post-action refresh completed."""
    print_section("30. Dashboard HTML Wires Up The Workflow Action Controls")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="wf-template"' in body
        assert 'id="wf-input"' in body
        assert 'id="wf-start-btn"' in body
        assert "/api/workflow-templates" in body
        assert "/api/workflow/start" in body
        assert "/api/workflow/next" in body
        assert "/api/workflow/complete" in body
        assert "/api/workflow/approve" in body
        assert "/api/workflow/retry" in body

        script_section = body.split("const wfLogs")[-1].split("async function loadStrategy")[0]
        assert "wf-next" in script_section
        assert "wf-retry" in script_section
        assert "wf-complete" in script_section
        assert "wf-approve" in script_section
        assert "wf-reject" in script_section
        # The persistence fix itself: the cache is read back into the
        # re-rendered card rather than left to default to empty.
        assert "wfLogs[w.instance_id]" in body
        assert "wfLogs[instanceId] = result.log" in script_section


def test_agent_run_endpoint_answers_a_real_question():
    """main_v2.py's `agent-run` command - the read-only tool-calling loop
    (agent_loop.py) that lets an agent look up real budgets/capacity/
    departments/task history instead of inventing an answer - has had zero
    web consumers since it shipped: a human on the dashboard could submit
    or process a task but had no way to ask the same question the CLI
    already answers. Confirms /api/agent-run calls the real run_agent(),
    not a placeholder: the mock provider (always available with no API
    key, see llm_provider.py) deterministically calls
    get_department_budget for a budget question, and the captured log -
    the same text the CLI prints - carries that real tool call and its
    real result, not a canned string."""
    print_section("31. POST /api/agent-run Answers A Real Question")

    with RunningServer() as server:
        status, body = server.post("/api/agent-run", {"question": "What is engineering's budget?"})
        print(f"  status={status} log={body.get('log')!r}")
        assert status == 200
        assert "get_department_budget(department='engineering')" in body["log"]
        assert "MOCK: answered using get_department_budget" in body["log"]
        assert "iteration(s)" in body["log"]


def test_agent_run_endpoint_validates_input():
    """Mirrors the CLI's own `agent-run` validation (main_v2.py's argument
    parsing for --max-iterations) rather than letting bad input reach
    run_agent_loop()'s own ValueError as an unhandled 500."""
    print_section("32. POST /api/agent-run Validates Its Input")

    with RunningServer() as server:
        status, body = server.post("/api/agent-run", {"question": "   "})
        print(f"  empty question -> status={status} body={body}")
        assert status == 400
        assert "question" in body["error"]

        status, body = server.post("/api/agent-run", {"question": "hi", "max_iterations": "not a number"})
        print(f"  non-numeric max_iterations -> status={status} body={body}")
        assert status == 400
        assert "max_iterations" in body["error"]

        status, body = server.post("/api/agent-run", {"question": "hi", "max_iterations": 0})
        print(f"  max_iterations=0 -> status={status} body={body}")
        assert status == 400
        assert "max_iterations" in body["error"]


def test_dashboard_html_wires_up_the_agent_run_panel():
    """Pins the frontend wiring that closes the gap the two tests above
    prove at the API layer: the panel, its question/max-iterations inputs
    and its log container exist, the click handler posts to
    /api/agent-run, and the response is rendered via textContent (same as
    the existing #log for submit/process) rather than innerHTML - this
    text is a transcript that can embed arbitrary tool-result content
    (e.g. a task description a user once typed), so it must never be
    interpreted as markup."""
    print_section("33. Dashboard HTML Wires Up The Ask-An-Agent Panel")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        assert 'id="f-question"' in body
        assert 'id="f-max-iterations"' in body
        assert 'id="agent-run-btn"' in body
        assert 'id="agent-log" hidden' in body
        assert "/api/agent-run" in body

        click_handler = body.split('$("#agent-run-btn").addEventListener')[-1].split("refreshAll();")[0]
        assert "/api/agent-run" in click_handler
        assert "result.log" in click_handler

        show_agent_log = body.split("function showAgentLog")[-1].split("\n}")[0]
        assert '$("#agent-log")' in show_agent_log
        assert "textContent = text" in show_agent_log
        assert "innerHTML" not in show_agent_log


def test_dashboard_html_escapes_recommendation_fields():
    """Checkpoint 79's own Next Steps named this gap: loadReport()'s
    #recs-list rendering interpolated r.agent/r.department, r.issue, and
    r.action directly into innerHTML, the only dynamic panel on this page
    that skipped escapeHtml() - every other interpolation (task detail,
    performance, resource, strategy, rollover, workflow, agent-run panels)
    already goes through it. Every recommendation today is server-generated
    from agent/department names already validated elsewhere (AgentState's
    own profile, set from config.json), so there was no live XSS path, but
    the inconsistency meant a future recommendation type keyed off anything
    less trusted would render unescaped by default. Pins that every field
    loadReport() reads off a recommendation is now escaped, mirroring
    test_dashboard_html_wires_up_the_budget_rollover_panel's shape."""
    print_section("34. Dashboard HTML Escapes Recommendation Fields")

    with RunningServer() as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            body = resp.read().decode("utf-8")

        script_section = body.split("async function loadReport")[-1].split("async function refreshAll")[0]
        assert "r.agent" in script_section
        assert "r.department" in script_section
        assert "r.issue" in script_section
        assert "r.action" in script_section
        assert "escapeHtml(r.agent || r.department)" in script_section
        assert "escapeHtml(r.issue)" in script_section
        assert "escapeHtml(r.action)" in script_section


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
        test_report_payload_resources_include_expense_breakdown_after_a_task_runs()
        test_dashboard_html_wires_up_the_resource_panel()
        test_strategy_endpoint_exposes_a_real_reallocation_proposal()
        test_dashboard_html_wires_up_the_strategy_panel()
        test_budget_rollovers_endpoint_exposes_a_real_recorded_rollover()
        test_dashboard_html_wires_up_the_budget_rollover_panel()
        test_workflows_endpoint_exposes_a_real_running_instance()
        test_dashboard_html_wires_up_the_workflow_panel()
        test_workflow_templates_endpoint_lists_the_real_registered_templates()
        test_workflow_start_endpoint_creates_a_real_instance()
        test_workflow_start_endpoint_validates_input()
        test_workflow_next_endpoint_advances_a_real_instance()
        test_workflow_complete_endpoint_executes_a_real_step()
        test_workflow_approve_endpoint_enforces_the_real_approval_role()
        test_workflow_retry_endpoint_reports_a_real_non_blocked_state()
        test_dashboard_html_wires_up_the_workflow_action_controls()
        test_agent_run_endpoint_answers_a_real_question()
        test_agent_run_endpoint_validates_input()
        test_dashboard_html_wires_up_the_agent_run_panel()
        test_dashboard_html_escapes_recommendation_fields()

    print("\n" + "=" * 60)
    print("  [OK] All web dashboard tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
