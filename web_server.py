"""
Web Dashboard - a visual, browser-based view onto the same state the CLI
(main_v2.py) reads and writes. Zero third-party dependencies, per this
project's constraint: the HTTP layer is entirely `http.server`/`socketserver`
(standard library), and the frontend (static/dashboard.html) is plain
HTML/CSS/vanilla JS with no build step and no CDN scripts.

Deliberately thin: every mutation (submitting a task, processing the queue)
calls straight into main_v2.py's own submit_task()/process_tasks() rather
than reimplementing them, so the dashboard and the CLI can never disagree
about what a submit or a process actually does - they're the same function
reading and writing the same data/*.json files. Read-only endpoints
(status/report) mirror the data main_v2.py's show_status()/show_report()
already print, just returned as JSON instead of formatted text.
"""
import contextlib
import io
import json
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import main_v2
from budgets import budget_manager, capacity_manager
from departments import DepartmentManager
from llm_provider import LLMProvider
from performance import analytics

STATIC_DIR = Path(__file__).parent / "static"
DASHBOARD_HTML_PATH = STATIC_DIR / "dashboard.html"

# Serializes calls that mutate task state (submit/process) AND that redirect
# stdout to capture main_v2's own print()-based trace (see
# _run_capturing_stdout). ThreadingHTTPServer runs one thread per request;
# contextlib.redirect_stdout swaps sys.stdout globally, so two concurrent
# writers would corrupt each other's captured output without this - not a
# hypothetical race, since a browser can fire the "Process Queue" button
# while a submit from another tab is still in flight.
_write_lock = threading.Lock()


def _tasks() -> list:
    main_v2.init_system()
    return json.loads(main_v2.TASKS_FILE.read_text())


def _task_summary(task: dict) -> dict:
    """A task without its (potentially large) result blob, for list views."""
    return {k: v for k, v in task.items() if k != "result"}


def _run_capturing_stdout(fn, *args, **kwargs):
    """Run fn under the write lock, returning (return_value, everything it
    printed). main_v2's CLI functions communicate outcomes (escalation
    reasons, per-task pass/fail) via print(), which a plain function call
    would otherwise discard - capturing it is how the dashboard shows the
    same story the terminal would, instead of a thinner one."""
    with _write_lock:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = fn(*args, **kwargs)
        return result, buf.getvalue()


def build_status_payload() -> dict:
    """Mirrors main_v2.show_status(), structured as JSON instead of printed."""
    tasks = _tasks()
    counts = {
        "total": len(tasks),
        "queued": sum(1 for t in tasks if t["status"] == "queued"),
        "completed": sum(1 for t in tasks if t["status"] == "completed"),
        "escalated": sum(1 for t in tasks if t["status"] == "escalated"),
        "failed": sum(1 for t in tasks if t["status"] == "failed"),
    }

    departments = []
    for dept in DepartmentManager.get_departments():
        budget = budget_manager.get_budget(dept)
        snapshot = capacity_manager.snapshot(dept)
        departments.append({
            "name": dept,
            "task_count": sum(1 for t in tasks if t["department"] == dept),
            "budget": None if not budget else {
                "allocated": budget.allocated,
                "spent": budget.spent,
                "reserved": budget.reserved_total(),
                "available": budget.available(),
                "utilization_pct": budget.utilization_pct(),
            },
            "capacity": None if snapshot.total_capacity <= 0 else {
                "agent_count": snapshot.agent_count,
                "workload": snapshot.current_workload,
                "total": snapshot.total_capacity,
                "utilization_pct": snapshot.utilization_pct(),
            },
        })

    return {
        "tasks": counts,
        "llm_provider": LLMProvider().get_status(),
        "departments": departments,
        "over_budget_departments": budget_manager.over_budget_departments(),
    }


def build_report_payload() -> dict:
    """Mirrors main_v2.show_report() (PerformanceAnalytics.generate_report()),
    structured as JSON instead of pre-formatted text."""
    system = analytics.get_system_metrics()
    return {
        "system": asdict(system),
        "top_performers": [
            {"name": name, "value": value}
            for name, value in analytics.get_top_performers("quality", 5)
        ],
        "recommendations": analytics.get_recommendations(),
        "resources": analytics.get_resource_summary(budgets=budget_manager, capacity=capacity_manager),
        "capacity_recommendations": capacity_manager.recommend_actions(),
    }


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "TeamAgentDashboard/1.0"

    def log_message(self, fmt, *args):
        # main_v2's own print()s (captured and echoed back in API responses)
        # are the audit trail this tool cares about; the default per-request
        # access log would just be noise on top of it.
        pass

    # ---- response helpers ----

    def _send_json(self, payload, status: int = 200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        """Returns {} for an empty body, a dict for valid JSON, or None for
        malformed JSON (the caller turns that into a 400)."""
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ---- routing ----

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(DASHBOARD_HTML_PATH.read_text())
        elif path == "/favicon.ico":
            # Every browser requests this unprompted; a 404 is harmless but
            # noisy in devtools for no reason - a quiet empty response reads
            # cleaner than a JSON 404 body for something nobody sent.
            self.send_response(204)
            self.end_headers()
        elif path == "/api/status":
            self._send_json(build_status_payload())
        elif path == "/api/report":
            self._send_json(build_report_payload())
        elif path == "/api/tasks":
            self._send_json([_task_summary(t) for t in _tasks()])
        elif path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            task = next((t for t in _tasks() if t["id"] == task_id), None)
            if task is None:
                self._send_json({"error": f"Task {task_id} not found"}, status=404)
            else:
                self._send_json(task)
        else:
            self._send_json({"error": f"no such route: GET {path}"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json_body()
        if body is None:
            self._send_json({"error": "malformed JSON body"}, status=400)
            return

        if path == "/api/submit":
            self._handle_submit(body)
        elif path == "/api/process":
            self._handle_process(body)
        else:
            self._send_json({"error": f"no such route: POST {path}"}, status=404)

    def _handle_submit(self, body: dict):
        description = (body.get("description") or "").strip()
        if not description:
            self._send_json({"error": "description is required"}, status=400)
            return

        department = body.get("department") or None
        estimated_hours = body.get("estimated_hours")
        if estimated_hours is not None:
            try:
                estimated_hours = float(estimated_hours)
            except (TypeError, ValueError):
                self._send_json({"error": "estimated_hours must be a number"}, status=400)
                return

        task_id, log = _run_capturing_stdout(
            main_v2.submit_task, description, department=department, estimated_hours=estimated_hours)

        if task_id is None:
            # submit_task() itself rejected the request (e.g. negative hours)
            # and printed exactly why - that printed reason IS the error.
            self._send_json({"error": log.strip() or "submission rejected"}, status=400)
            return

        task = next(t for t in _tasks() if t["id"] == task_id)
        self._send_json({"task": task, "log": log}, status=201)

    def _handle_process(self, body: dict):
        sequential = bool(body.get("sequential", False))
        _, log = _run_capturing_stdout(main_v2.process_tasks, parallel=not sequential)
        self._send_json({"tasks": [_task_summary(t) for t in _tasks()], "log": log})


def run(port: int = 8765, bind: str = "127.0.0.1"):
    main_v2.init_system()
    server = ThreadingHTTPServer((bind, port), DashboardRequestHandler)
    actual_port = server.server_address[1]
    print(f"Team Agent System dashboard: http://{bind}:{actual_port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
