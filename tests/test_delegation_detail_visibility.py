#!/usr/bin/env python3
"""
A real delegation's result carries two fields neither human-facing renderer
has ever shown: result["delegated_from"] (task_executor_v2.py:361, the
immediate delegator of whichever agent actually ran the task) and
result["delegation_reasoning"] (task_executor_v2.py:363, the free-text
reason find_best_delegate()'s caller gave for the hand-off - e.g.
"Delegating to Design Head who has higher affinity (0.9 difference)").
Both have existed since delegation execution first shipped and are already
covered by tests/test_delegation.py and tests/test_delegate_to_tool.py as
"was it set on the result dict" assertions - neither test, nor anything
else in the tree, reads them back out of a rendered task. `grep -rn
"delegated_from\|delegation_reasoning"` outside those declarations and
test assertions turns up nothing in main_v2.py or static/dashboard.html:
both print/render delegation_chain (the full hand-off path) right next to
where these two fields sit in the same result dict, and skip past both.

Confirmed live before this fix: running a real two-department delegation
(engineering_head dislikes its own work, design_head is the better
candidate) through the actual production code path -
DepartmentHeadAgent.run() -> AgentDecisionEngine.find_best_delegate() ->
DepartmentHeadAgent.delegate_task() - produced a result with
delegated_from="engineering_head" and a full delegation_reasoning
sentence. Feeding that exact result into main_v2.show_task()'s captured
stdout had no "Delegated From" or "Delegation Reasoning" line - only the
pre-existing "Delegation Chain" line, which (for a single-hop delegation)
happens to contain the same agent_id string as delegated_from by
coincidence, not because the field was actually surfaced. The reasoning
sentence - the one piece of this that explains *why* the hand-off
happened - had no surface at all.

Fixed: main_v2.show_task() and static/dashboard.html's renderTaskDetail()
each gained two .get()-guarded lines/entries, "Delegated From" and
"Delegation Reasoning", placed directly after the existing "Delegation
Chain" line/entry - same absence-means-never-delegated convention every
other optional field in both renderers already follows.

Not fixed because it was already fine: web_server.py's GET /api/tasks/<id>
returns the task dict unmodified (no field allowlist, confirmed first at
checkpoint 72 for result["metrics"]) - this test pins that same fact for
these two fields so a future field-allowlist refactor there doesn't
quietly drop them.
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
from agent_decisions import AgentDecisionEngine  # noqa: F401 - exercised via DepartmentHeadAgent.run()
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import TaskExecutor

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


def _profile(agent_id: str, department: str, skill: int = 5) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=["shared"], skill_level=skill,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    )


def _run_a_real_delegation(suffix: str) -> dict:
    """Same shape as tests/test_delegation.py's _make_executor(): an
    isolated registry/budget/capacity/analytics set (own data files, never
    the shared production singletons), engineering strongly dislikes its
    own work so should_execute() hands off, design is the only other
    candidate so find_best_delegate() always picks it deterministically."""
    registry = AgentRegistry(data_file=f"data/test_deleg_vis_agents_{suffix}.json")
    for department, disliked in [("engineering", True), ("design", False)]:
        agent_id = f"{department}_head"
        state = registry.register(_profile(agent_id, department))
        state.current_workload = 0
        state.learned_preferences = {department: -0.9} if disliked else {}
        state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_deleg_vis_budgets_{suffix}.json")
    for department in ("engineering", "design"):
        budgets.allocate(department, 100000)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_deleg_vis_metrics_{suffix}.json"),
    )
    return executor.get_agent_for_department("engineering").run("Build a thing", 2.0)


def _inject_task(task_id: str, department: str, description: str, result: dict):
    main_v2.init_system()
    tasks = json.loads(main_v2.TASKS_FILE.read_text())
    tasks.append({
        "id": task_id, "status": "completed", "department": department,
        "description": description, "created_at": "now", "result": result,
    })
    main_v2.TASKS_FILE.write_text(json.dumps(tasks, indent=2))


def test_show_task_prints_delegated_from_and_reasoning_for_a_real_delegation():
    print_section("1. show_task() Prints A Real Delegation's From/Reasoning")

    result = _run_a_real_delegation("a")
    print(f"  agent_id             : {result.get('agent_id')}")
    print(f"  delegated_from       : {result.get('delegated_from')}")
    print(f"  delegation_chain     : {result.get('delegation_chain')}")
    print(f"  delegation_reasoning : {result.get('delegation_reasoning')}")

    assert result["agent_id"] == "design_head", "the delegate must be the one that ran"
    assert result["delegated_from"] == "engineering_head"
    assert result["delegation_reasoning"], "a real hand-off must carry a non-empty reason"

    with IsolatedCwd():
        _inject_task("0001", "engineering", "Build a thing", result)
        _, output = _captured(main_v2.show_task, "0001")
        print(output)

        assert f"Delegated From: {result['delegated_from']}" in output
        assert f"Delegation Reasoning: {result['delegation_reasoning']}" in output


def test_show_task_omits_both_lines_for_a_task_that_was_never_delegated():
    print_section("2. show_task() Omits Both Lines When Nothing Was Delegated")

    with IsolatedCwd():
        task_id = main_v2.submit_task("Quick typo fix in docs", estimated_hours=1.0)
        main_v2.process_tasks(parallel=False)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        task = next(t for t in tasks if t["id"] == task_id)
        assert "delegated_from" not in task["result"], (
            "this task must not have actually delegated, or the negative check below is vacuous")

        _, output = _captured(main_v2.show_task, task_id)
        print(output)

        assert "Delegated From" not in output
        assert "Delegation Reasoning" not in output


def test_dashboard_html_renders_the_same_two_fields():
    print_section("3. dashboard.html's JS Mirrors The Same Fix")

    dashboard = open(os.path.join(REPO_ROOT, "static", "dashboard.html")).read()
    print(f"  dashboard.html length: {len(dashboard)} bytes")

    assert re.search(r"if\s*\(result\.delegated_from\)\s*\{", dashboard)
    assert re.search(r"if\s*\(result\.delegation_reasoning\)\s*\{", dashboard)
    assert "Delegated From" in dashboard
    assert "Delegation Reasoning" in dashboard
    # Both must be HTML-escaped - delegation_reasoning is free text built
    # from a department's config-declared name, and nothing upstream
    # guarantees it is free of "<"/"&" forever.
    assert "escapeHtml(result.delegated_from)" in dashboard
    assert "escapeHtml(result.delegation_reasoning)" in dashboard


def test_api_tasks_detail_already_carried_delegation_fields_unmodified():
    print_section("4. GET /api/tasks/<id> Already Passed Both Fields Through")

    result = _run_a_real_delegation("b")

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

        print(f"  status={status} delegated_from={body.get('result', {}).get('delegated_from')}")
        assert status == 200
        assert body["result"]["delegated_from"] == result["delegated_from"]
        assert body["result"]["delegation_reasoning"] == result["delegation_reasoning"]
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_show_task_prints_delegated_from_and_reasoning_for_a_real_delegation()
    test_show_task_omits_both_lines_for_a_task_that_was_never_delegated()
    test_dashboard_html_renders_the_same_two_fields()
    test_api_tasks_detail_already_carried_delegation_fields_unmodified()
    print("\n✅ All delegation-detail-visibility tests passed!")
