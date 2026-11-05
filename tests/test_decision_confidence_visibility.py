#!/usr/bin/env python3
"""
AgentDecisionEngine.decide() has computed a real, varying DecisionResult.confidence
(0.6 for an engine-level escalation, 0.75/0.8 for a delegation, up to 0.95 for
an execution, scaled by the deciding agent's own skill_level) since Phase 1 -
agent_decisions.py never changed. task_executor_v2.py's decide_on_task() even
puts it in the dict it returns ("confidence": decision.confidence, alongside
"decision"/"assigned_agent_id"/"requires_approval"). But `grep -n
'decision_info\['` on _run_locked(), decide_on_task()'s only caller, shows it
reads back exactly two keys - "raw_decision" and "context" - never
"confidence". From there the value only ever reached the ephemeral
"agent_decision" trace event (core.py's EventBus, gone once the process
exits) - not the task's persisted result, not any CLI output, not the
dashboard, not any API payload. A human had no way to see how sure the
decision engine was about ANY decision it made - executed, delegated, or
escalated - confirmed live below before this fix (data/tasks.json's result
had no "confidence" key at all for a plain completed task).

Fixed: every result dict _run_locked() builds (escalate via can_execute()/
find_best_delegate(), escalate via budget denial, the iteration-exhausted
path, and the normal completed path) now carries "confidence":
decision.confidence, same additive, .get()-guarded convention as
quality_score/delegation_reasoning. _DelegationRequest gained an optional
`confidence` field so an engine-driven delegation's OWN confidence (in the
decision to hand off, not the eventual executor's) survives the hand-off
too, under a separate "delegation_confidence" key - mirroring how
delegation_reasoning is already kept distinct from the executing agent's own
reasoning. The mid-reasoning delegate_to TOOL path has no DecisionResult to
read a confidence from and deliberately keeps delegation_confidence unset,
pinned by test 4 below so a future "just default it to something" patch
doesn't quietly fabricate a number that was never computed.

main_v2.show_task() and static/dashboard.html's renderTaskDetail() each
gained a "Decision Confidence"/"Delegation Confidence" line/entry, same
absence-means-never-computed convention every other optional field in both
renderers already follows. web_server.py needed no change: GET
/api/tasks/<id> already returns the task dict unmodified (confirmed first at
checkpoint 72, pinned again by test_delegation_detail_visibility.py for the
delegation fields) - test 5 below pins the same fact for these two new ones.

Left out of scope, deliberately: /api/tasks (the list view) and its
_task_summary() helper. quality_score/token_cost are pulled to that
summary's top level because dashboard.html's Tasks table already has a
column that renders them (the "Result" badge); confidence has no such
column today, and adding one is a UI design decision this session isn't
making. The detail view (GET /api/tasks/<id>, already unmodified) is where
this fix actually lands a value a human can see.
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


def _inject_task(task_id: str, department: str, description: str, result: dict):
    main_v2.init_system()
    tasks = json.loads(main_v2.TASKS_FILE.read_text())
    tasks.append({
        "id": task_id, "status": result.get("status", "completed"), "department": department,
        "description": description, "created_at": "now", "result": result,
    })
    main_v2.TASKS_FILE.write_text(json.dumps(tasks, indent=2))


def test_show_task_prints_confidence_for_a_real_completed_task():
    """A plain, non-delegated, non-escalated task - the common case."""
    print_section("1. show_task() Prints Decision Confidence For A Completed Task")

    with IsolatedCwd():
        task_id = main_v2.submit_task("Write a routine status update", estimated_hours=2.0)
        main_v2.process_tasks(parallel=False)

        tasks = json.loads(main_v2.TASKS_FILE.read_text())
        task = next(t for t in tasks if t["id"] == task_id)
        result = task["result"]
        print(f"  status     : {result.get('status')}")
        print(f"  confidence : {result.get('confidence')}")

        assert result["status"] == "completed"
        assert result.get("confidence") is not None, (
            "a completed task's decision confidence must be persisted")
        assert 0.0 < result["confidence"] <= 0.95

        _, output = _captured(main_v2.show_task, task_id)
        print(output)
        assert f"Decision Confidence: {result['confidence']:.0%}" in output


def test_show_task_prints_confidence_for_an_engine_level_escalation():
    """No delegate candidate exists anywhere in the registry (only one agent
    is registered at all) and that sole agent is already at its concurrency
    limit - can_execute() refuses for capacity, find_best_delegate() comes
    back empty, and AgentDecisionEngine.decide() escalates with its own
    hardcoded confidence=0.6 (agent_decisions.py's can-execute-false/
    no-delegate branch). This exercises _run_locked()'s OWN escalate return,
    not _dispatch_delegation()'s separate cycle/depth-limit escalate (which
    has no DecisionResult in scope and correctly never gains a confidence)."""
    print_section("2. show_task() Prints Decision Confidence For An Engine-Level Escalation")

    registry = AgentRegistry(data_file="data/test_dcv_solo_agents.json")
    state = registry.register(_profile("engineering_head", "engineering", skill=4))
    state.current_workload = state.profile.max_concurrent_tasks  # already full
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_dcv_solo_budgets.json")
    budgets.allocate("engineering", 100000)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_dcv_solo_metrics.json"),
    )
    result = executor.get_agent_for_department("engineering").run("Ship a feature", 2.0)
    print(f"  status     : {result.get('status')}")
    print(f"  confidence : {result.get('confidence')}")

    assert result["status"] == "escalated"
    assert result["confidence"] == 0.6, "can_execute()-false/no-delegate escalation's own hardcoded value"

    with IsolatedCwd():
        _inject_task("0001", "engineering", "Ship a feature", result)
        _, output = _captured(main_v2.show_task, "0001")
        print(output)
        assert "Decision Confidence: 60%" in output


def test_engine_driven_delegation_keeps_delegation_confidence_distinct_from_the_delegates_own():
    """engineering_head dislikes its own work (learned_preferences < -0.5),
    so should_execute() hands off; design_head is the only other registered
    agent, so find_best_delegate() always picks it. The delegator's OWN
    confidence in deciding to delegate (0.75, agent_decisions.py's
    should-execute-false/delegate-found branch) and the delegate's OWN
    confidence in actually executing (skill_level 5 -> min(0.95, 0.5+0.5))
    are two different numbers about two different decisions - this pins
    that the fix keeps them under separate keys rather than one clobbering
    the other."""
    print_section("3. A Real Delegation Keeps Both Agents' Confidence Separate")

    registry = AgentRegistry(data_file="data/test_dcv_deleg_agents.json")
    for department, disliked in [("engineering", True), ("design", False)]:
        agent_id = f"{department}_head"
        state = registry.register(_profile(agent_id, department))
        state.current_workload = 0
        state.learned_preferences = {department: -0.9} if disliked else {}
        state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_dcv_deleg_budgets.json")
    for department in ("engineering", "design"):
        budgets.allocate(department, 100000)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_dcv_deleg_metrics.json"),
    )
    result = executor.get_agent_for_department("engineering").run("Build a thing", 2.0)
    print(f"  executed by            : {result.get('agent_id')}")
    print(f"  confidence (delegate)  : {result.get('confidence')}")
    print(f"  delegation_confidence  : {result.get('delegation_confidence')}")

    assert result["status"] == "completed"
    assert result["agent_id"] == "design_head"
    assert result["delegation_confidence"] == 0.75, "the delegator's own should-execute-false confidence"
    assert result["confidence"] == 0.95, "the delegate's own skill_level-5 execution confidence"
    assert result["confidence"] != result["delegation_confidence"]

    with IsolatedCwd():
        _inject_task("0001", "engineering", "Build a thing", result)
        _, output = _captured(main_v2.show_task, "0001")
        print(output)
        assert "Decision Confidence: 95%" in output
        assert "Delegation Confidence: 75%" in output


def test_mid_reasoning_delegate_to_call_has_no_delegation_confidence():
    """The delegate_to TOOL call (tests/test_delegate_to_tool.py's own
    territory) never goes through AgentDecisionEngine.decide() at all - the
    model decides mid-reasoning, inside the tool loop - so there is no
    DecisionResult to read a delegation confidence from. This pins that
    absence as intentional: the delegate's own execution confidence must
    still be present, but delegation_confidence must not be fabricated."""
    print_section("4. A Tool-Initiated Delegation Has No Delegation Confidence")

    class DelegatesToProvider:
        def __init__(self, department):
            self.department = department
            self.turns = 0

        def supports_tools(self, task_type="general"):
            return True

        def generate_with_tools(self, messages, tool_schemas, task_type="general"):
            self.turns += 1
            if self.turns == 1:
                return {
                    "content": "", "tool_calls": [
                        {"id": "c1", "name": "delegate_to",
                         "arguments": {"department": self.department, "reason": "not my team"}}],
                    "provider": "fake", "model": "FakeModel", "tokens_estimate": 12,
                }
            return {"content": "fallback", "tool_calls": [], "provider": "fake", "model": "FakeModel",
                    "tokens_estimate": 5}

    class ScriptedProvider:
        def supports_tools(self, task_type="general"):
            return True

        def generate_with_tools(self, messages, tool_schemas, task_type="general"):
            return {"content": "Handled.", "tool_calls": [], "provider": "fake", "model": "FakeModel",
                    "tokens_estimate": 5}

    registry = AgentRegistry(data_file="data/test_dcv_tooldeleg_agents.json")
    for department in ("support", "engineering"):
        state = registry.register(_profile(f"{department}_head", department, skill=4))
        state.current_workload = 0
        state.learned_preferences = {}
        state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_dcv_tooldeleg_budgets.json")
    for department in ("support", "engineering"):
        budgets.allocate(department, 100000)

    providers = {"support": DelegatesToProvider("engineering"), "engineering": ScriptedProvider()}

    class _Provider:
        def supports_tools(self, task_type="general"):
            return providers[task_type].supports_tools(task_type)

        def generate_with_tools(self, messages, tool_schemas, task_type="general"):
            return providers[task_type].generate_with_tools(messages, tool_schemas, task_type)

    executor = TaskExecutor(
        _Provider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_dcv_tooldeleg_metrics.json"),
    )
    result = executor.get_agent_for_department("support").run(
        "Design a new architecture for the platform", 3.0)
    print(f"  executed by            : {result.get('agent_id')}")
    print(f"  confidence (delegate)  : {result.get('confidence')}")
    print(f"  delegation_confidence  : {result.get('delegation_confidence')}")

    assert result["status"] == "completed"
    assert result["agent_id"] == "engineering_head"
    assert result["confidence"] == 0.9, "engineering_head's own skill_level-4 execution confidence"
    assert "delegation_confidence" not in result, (
        "the delegate_to tool path has no DecisionResult, so this must stay absent, not None/0")


def test_dashboard_html_renders_both_new_fields():
    print_section("5. dashboard.html's JS Renders Both New Fields")

    dashboard = open(os.path.join(REPO_ROOT, "static", "dashboard.html")).read()
    assert re.search(r"if\s*\(result\.confidence\s*!=\s*null\)\s*\{", dashboard)
    assert re.search(r"if\s*\(result\.delegation_confidence\s*!=\s*null\)\s*\{", dashboard)
    assert "Decision Confidence" in dashboard
    assert "Delegation Confidence" in dashboard


def test_api_tasks_detail_passes_both_new_fields_through_unmodified():
    print_section("6. GET /api/tasks/<id> Passes Both Fields Through Untouched")

    registry = AgentRegistry(data_file="data/test_dcv_api_agents.json")
    for department, disliked in [("engineering", True), ("design", False)]:
        agent_id = f"{department}_head"
        state = registry.register(_profile(agent_id, department))
        state.current_workload = 0
        state.learned_preferences = {department: -0.9} if disliked else {}
        state.metrics = PerformanceMetrics()
    budgets = BudgetManager(data_file="data/test_dcv_api_budgets.json")
    for department in ("engineering", "design"):
        budgets.allocate(department, 100000)
    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_dcv_api_metrics.json"),
    )
    result = executor.get_agent_for_department("engineering").run("Build a thing", 2.0)
    assert result.get("delegation_confidence") is not None, "sanity: this must be a real delegation"

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

        print(f"  status={status} confidence={body.get('result', {}).get('confidence')} "
              f"delegation_confidence={body.get('result', {}).get('delegation_confidence')}")
        assert status == 200
        assert body["result"]["confidence"] == result["confidence"]
        assert body["result"]["delegation_confidence"] == result["delegation_confidence"]
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_show_task_prints_confidence_for_a_real_completed_task()
    test_show_task_prints_confidence_for_an_engine_level_escalation()
    test_engine_driven_delegation_keeps_delegation_confidence_distinct_from_the_delegates_own()
    test_mid_reasoning_delegate_to_call_has_no_delegation_confidence()
    test_dashboard_html_renders_both_new_fields()
    test_api_tasks_detail_passes_both_new_fields_through_unmodified()

    print("\n✅ All decision-confidence-visibility tests passed!")
