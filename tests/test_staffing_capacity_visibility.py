#!/usr/bin/env python3
"""
task_executor_v2.py's _run_locked() has computed a real department-capacity
reading (utilization_pct, agent_count, slack, over_capacity) every time it
runs, since the capacity_check trace event was added - and used that exact
reading to pick _effective_staff_count()'s staff_count, which becomes
staff_contributions' own length. But the reading itself never reached
anywhere a human reads later: only the "capacity_check" trace event (core.py's
EventBus, gone once the process exits) ever saw it. Confirmed live before this
fix: a completed task's persisted result had staff_contributions (5 entries
for an idle department) with no key anywhere - "capacity", "utilization", or
otherwise - explaining why 5 rather than some other number.

CapacitySnapshot.timestamp (budgets.py) has existed since the capacity
module's own first commit and had zero consumers anywhere in the repo,
including every test file - grep for it outside its own dataclass definition
and sort_by call returned nothing. It is included here as "as_of", dating
the reading to the moment this specific task was staffed rather than
confusing it with "right now" on a live dashboard view.

Fixed: _run_locked()'s completed-task return dict now carries a
"staffing_capacity" dict (utilization_pct/agent_count/slack/over_capacity/
as_of), read from the exact same CapacitySnapshot object (and the same
is_over_capacity() call, now computed once and reused instead of twice)
already driving staff_count - not a second, possibly different reading.

Left out of scope, deliberately: both escalate-before-staffing return paths
(engine-level escalation via can_execute()/find_best_delegate(), and budget
denial) return before capacity or staff_count are even computed, so there is
nothing to attach - pinned by test 2 below. The iteration-exhausted path DOES
compute staff_count (for consistency with the agent's workload bookkeeping)
but, like staff_contributions itself, was already deliberately excluded from
that smaller return dict before this session - a task that never finished
has no staffing figure worth dating, the same reasoning that keeps
staff_contributions out of it too.

main_v2.show_task() and static/dashboard.html's renderTaskDetail() each
gained a "Staffing Capacity" line/entry, same absence-means-never-computed
convention every other optional field in both renderers already follows.
web_server.py needed no change - GET /api/tasks/<id> already returns the
task dict unmodified (confirmed first at checkpoint 72) - test 5 below pins
that fact for this field too.
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
from http.server import ThreadingHTTPServer

import main_v2
import web_server
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from core import EventBus
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent, TaskExecutor

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


def _profile(agent_id: str, department: str, skill: int = 5, max_concurrent: int = 3) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=["shared"], skill_level=skill,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=max_concurrent,
    )


def _inject_task(task_id: str, department: str, description: str, result: dict):
    main_v2.init_system()
    tasks = json.loads(main_v2.TASKS_FILE.read_text())
    tasks.append({
        "id": task_id, "status": result.get("status", "completed"), "department": department,
        "description": description, "created_at": "now", "result": result,
    })
    main_v2.TASKS_FILE.write_text(json.dumps(tasks, indent=2))


def test_show_task_prints_staffing_capacity_for_a_real_completed_task():
    """A plain, non-delegated, non-escalated task - the common case."""
    print_section("1. show_task() Prints Staffing Capacity For A Completed Task")

    with IsolatedCwd():
        task_id = main_v2.submit_task("Write a routine status update", estimated_hours=2.0)
        main_v2.process_tasks(parallel=False)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        task = next(t for t in tasks if t["id"] == task_id)
        result = task["result"]
        print(f"  status            : {result.get('status')}")
        print(f"  staffing_capacity : {result.get('staffing_capacity')}")

        assert result["status"] == "completed"
        sc = result.get("staffing_capacity")
        assert sc is not None, "a completed task's staffing-capacity reading must be persisted"
        for key in ("utilization_pct", "agent_count", "slack", "over_capacity", "as_of"):
            assert key in sc, f"staffing_capacity is missing {key!r}"
        assert isinstance(sc["agent_count"], int) and sc["agent_count"] >= 1
        assert isinstance(sc["over_capacity"], bool)
        assert 0.0 <= sc["utilization_pct"] <= 1.0
        assert sc["as_of"], "as_of must be a real, non-empty timestamp"

        _, output = _captured(main_v2.show_task, task_id)
        print(output)
        assert "Staffing Capacity:" in output
        assert f"{sc['utilization_pct']:.0%} utilized" in output
        assert f"{sc['agent_count']} agent(s)" in output
        assert f"{sc['slack']} slack" in output
        assert sc["as_of"] in output


def test_staffing_capacity_is_absent_for_escalation_paths_that_never_reach_it():
    """Both escalate-before-staffing returns (can_execute()-false/no-delegate,
    and budget denial) return long before capacity or staff_count are
    computed at all - staffing_capacity must stay absent, not a fabricated
    zeroed dict, same convention confidence/staff_contributions already
    follow for these same two branches."""
    print_section("2. Staffing Capacity Is Absent For Escalations That Never Compute It")

    registry = AgentRegistry(data_file="data/test_stcap_solo_agents.json")
    state = registry.register(_profile("engineering_head", "engineering", skill=4))
    state.current_workload = state.profile.max_concurrent_tasks  # already full
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_stcap_solo_budgets.json")
    budgets.allocate("engineering", 100000)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_stcap_solo_metrics.json"),
    )
    result = executor.get_agent_for_department("engineering").run("Ship a feature", 2.0)
    print(f"  status            : {result.get('status')}")
    print(f"  staffing_capacity : {result.get('staffing_capacity')}")

    assert result["status"] == "escalated"
    assert "staffing_capacity" not in result, (
        "the engine-level escalation returns before staffing is ever computed")


def test_staffing_capacity_reports_a_real_over_capacity_department_truthfully():
    """A department at 90% utilization (over the 0.85 default threshold) must
    show over_capacity=True in the persisted reading, matching
    CapacityManager.is_over_capacity() exactly - the same single snapshot and
    the same is_over_capacity() verdict the capacity_check trace event
    reports, not a second, possibly-divergent read."""
    print_section("3. Staffing Capacity Reports A Real Over-Capacity Department")

    registry = AgentRegistry(data_file="data/test_stcap_busy_agents.json")
    state = registry.register(_profile("engineering_head", "engineering", skill=3, max_concurrent=10))
    state.current_workload = 9  # 90%, over the 0.85 default threshold
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_stcap_busy_budgets.json")
    budgets.allocate("engineering", 100000)
    capacity_manager = CapacityManager(registry=registry)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=capacity_manager,
        analytics=PerformanceAnalytics(data_file="data/test_stcap_busy_metrics.json"),
    )
    agent = executor.get_agent_for_department("engineering")
    bus = EventBus()
    agent.bus = bus
    result = agent.run("Ship a feature", 1.0)

    events = bus.get_traces("capacity_check")
    assert len(events) == 1
    print(f"  trace over_capacity    : {events[0].data.get('over_capacity')}")
    print(f"  staffing_capacity      : {result.get('staffing_capacity')}")

    assert result["status"] == "completed"
    sc = result["staffing_capacity"]
    assert sc["over_capacity"] is True
    assert sc["over_capacity"] == capacity_manager.is_over_capacity("engineering")
    assert sc["over_capacity"] == events[0].data.get("over_capacity"), (
        "the persisted reading and the trace event must agree - same snapshot")
    assert sc["utilization_pct"] == events[0].data["utilization"]

    with IsolatedCwd():
        _inject_task("0001", "engineering", "Ship a feature", result)
        _, output = _captured(main_v2.show_task, "0001")
        print(output)
        assert "[OVER CAPACITY]" in output


def test_dashboard_html_renders_staffing_capacity():
    print_section("4. dashboard.html's JS Renders Staffing Capacity")

    dashboard = open(os.path.join(REPO_ROOT, "static", "dashboard.html")).read()
    assert re.search(r"if\s*\(result\.staffing_capacity\)\s*\{", dashboard)
    assert "Staffing Capacity" in dashboard
    assert "OVER CAPACITY" in dashboard


def test_api_tasks_detail_passes_staffing_capacity_through_unmodified():
    print_section("5. GET /api/tasks/<id> Passes Staffing Capacity Through Untouched")

    registry = AgentRegistry(data_file="data/test_stcap_api_agents.json")
    state = registry.register(_profile("engineering_head", "engineering"))
    state.current_workload = 0
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()
    budgets = BudgetManager(data_file="data/test_stcap_api_budgets.json")
    budgets.allocate("engineering", 100000)
    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_stcap_api_metrics.json"),
    )
    result = executor.get_agent_for_department("engineering").run("Build a thing", 2.0)
    assert result.get("staffing_capacity") is not None, "sanity: must be a real completed task"

    original_cwd = os.getcwd()
    tmpdir = tempfile.mkdtemp()
    try:
        shutil.copy(os.path.join(REPO_ROOT, "config.json"), tmpdir)
        os.chdir(tmpdir)
        _inject_task("0001", "engineering", "Build a thing", result)

        server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.DashboardRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/tasks/0001", timeout=10) as resp:
                status = resp.status
                body = json.loads(resp.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

        print(f"  status={status} staffing_capacity={body.get('result', {}).get('staffing_capacity')}")
        assert status == 200
        assert body["result"]["staffing_capacity"] == result["staffing_capacity"]
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_show_task_prints_staffing_capacity_for_a_real_completed_task()
    test_staffing_capacity_is_absent_for_escalation_paths_that_never_reach_it()
    test_staffing_capacity_reports_a_real_over_capacity_department_truthfully()
    test_dashboard_html_renders_staffing_capacity()
    test_api_tasks_detail_passes_staffing_capacity_through_unmodified()

    print("\n✅ All staffing-capacity-visibility tests passed!")
