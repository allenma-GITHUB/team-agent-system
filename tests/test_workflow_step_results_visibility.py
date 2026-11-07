#!/usr/bin/env python3
"""
workflows.py's complete_step()/execute_step() have always stored a step's
real execution result - the same dict TaskExecutor.execute() returns for a
submitted task (summary, quality_score, tokens_used, token_cost, status,
...) - into instance.step_results[step_id], and save()/load() have always
round-tripped that dict to disk on every save. But get_status() - the one
shape main_v2.py's `workflow status <id>` CLI command, static/dashboard.html's
workflow cards, and web_server.py's /api/workflows route (which returns
get_status()'s dict unmodified - build_workflows_payload()) all read - never
included step_results at all, only step_statuses (the bare status word per
step: "completed", "pending", ...).

Confirmed live before this fix: starting a real `bug_fix` instance and
completing its first step through main_v2.workflow_complete() (the exact CLI
path `workflow complete <id> <step>` drives) left a real result dict - with
a real summary - sitting in instance.step_results["triage"], persisted to
data/workflows.json. But engine.get_status(instance_id) had no
"step_results" key at all, and main_v2.workflow_status()'s captured stdout
(the command whose whole job is reporting an instance's status later, not
just at the moment a step finishes) printed only "triage  completed" -
nothing about what that step actually produced, cost, or how it scored.
The CLI's only other mention of a step's result (workflow_complete()'s own
one-shot print, right after execute_step() returns) is gone the moment that
command exits; `workflow status` immediately afterward already couldn't see
it.

Fixed: workflows.py's get_status() now includes step_results alongside
step_statuses - the exact dict already sitting on the WorkflowInstance
object and already round-tripped by save()/load(), not a second,
possibly-divergent read. main_v2.py's workflow_status() and
static/dashboard.html's loadWorkflows() each gained a per-step detail line
(summary, plus quality_score/token_cost when present) printed right under
that step's status word, guarded the same "no value yet" way every other
optional field in both renderers already follows - a step with no result
yet (pending, in_progress with no result stored) renders exactly as before.
web_server.py needed no change to its route handler: build_workflows_payload()
already returns get_status()'s dict unmodified, so the new key reaches
/api/workflows for free - only its stale docstring (naming the exact keys
get_status() returns) was corrected to mention step_results.
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
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate

DATA_FILE = "data/test_workflow_step_results.json"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class IsolatedCwd:
    """Chdir's to a scratch dir with a copy of the real config.json, so
    main_v2's module-level workflow_engine (which saves to the relative
    path "data/workflows.json") never touches the repo's own data files."""

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


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_sr_test", name="Step-result test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_sr"),
        ]
    )


def _cleanup():
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)


def test_get_status_includes_step_results_matching_the_real_object():
    print_section("1. get_status() Carries step_results, Not Just step_statuses")
    _cleanup()
    try:
        engine = WorkflowEngine(data_file=DATA_FILE)
        engine.register_template(make_template())
        instance = engine.create_instance("wf_sr_test", {})
        engine.start_instance(instance.instance_id)

        real_result = {"status": "completed", "summary": "Intake handled by qa_sr",
                        "quality_score": 4.5, "tokens_used": 120, "token_cost": 0.0024}
        ok = engine.complete_step(instance.instance_id, "intake", real_result)
        assert ok

        print(f"  real object step_results: {instance.step_results}")
        status = engine.get_status(instance.instance_id)
        print(f"  get_status() keys: {sorted(status.keys())}")
        assert "step_results" in status, "get_status() dropped step_results entirely"
        assert status["step_results"] == instance.step_results
        assert status["step_results"]["intake"]["summary"] == "Intake handled by qa_sr"
    finally:
        _cleanup()

    print("\n✅ get_status() includes step_results")


def test_cli_workflow_status_prints_step_result_detail():
    print_section("2. CLI `workflow status` Prints Each Step's Real Result")

    with IsolatedCwd():
        instance_id = main_v2.workflow_start("bug_fix", ["severity=high"])
        step = main_v2.workflow_engine.get_next_step(instance_id)
        assert step is not None

        _, complete_output = _captured(main_v2.workflow_complete, instance_id, step.step_id)
        print(complete_output)

        instance = main_v2.workflow_engine.get_instance(instance_id)
        result = instance.step_results[step.step_id]
        assert result.get("summary"), "sanity: the real executor must have produced a summary"

        _, status_output = _captured(main_v2.workflow_status, instance_id)
        print(status_output)

        assert f"{step.step_id:<20} completed" in status_output
        assert result["summary"] in status_output, (
            "workflow status (run after the command that produced the result, "
            "not just the one that printed it once) must still show it")
    print("\n✅ CLI workflow status prints the step's real result")


def test_cli_workflow_status_omits_detail_for_steps_with_no_result_yet():
    print_section("3. CLI `workflow status` Shows No Fabricated Detail For Unstarted Steps")

    with IsolatedCwd():
        instance_id = main_v2.workflow_start("bug_fix", ["severity=high"])
        _, status_output = _captured(main_v2.workflow_status, instance_id)
        print(status_output)

        assert "fix                  pending" in status_output
        lines = status_output.splitlines()
        fix_idx = next(i for i, l in enumerate(lines) if l.strip().startswith("fix"))
        # The line right after a never-run step's status word must not be an
        # indented result-detail line - there is nothing to report yet.
        assert fix_idx + 1 == len(lines) or not lines[fix_idx + 1].startswith("    ")
    print("\n✅ No fabricated detail for a step with no result yet")


def test_dashboard_html_renders_step_result_detail():
    print_section("4. Dashboard HTML Renders Per-Step Result Detail")
    with open(os.path.join(REPO_ROOT, "static", "dashboard.html"), encoding="utf-8") as f:
        body = f.read()

    script_section = body.split("async function loadWorkflows")[-1].split("async function postWorkflow")[0]
    print(f"  loadWorkflows() section length: {len(script_section)} chars")

    assert "w.step_results" in script_section
    assert "result.summary" in script_section
    assert "result.quality_score" in script_section

    print("\n✅ Dashboard renders step_results detail")


def test_api_workflows_passes_step_results_through_unmodified():
    print_section("5. GET /api/workflows Passes step_results Through Untouched")

    with IsolatedCwd():
        instance_id = main_v2.workflow_start("bug_fix", ["severity=high"])
        step = main_v2.workflow_engine.get_next_step(instance_id)
        main_v2.workflow_complete(instance_id, step.step_id)
        instance = main_v2.workflow_engine.get_instance(instance_id)
        expected = instance.step_results

        server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.DashboardRequestHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/workflows", timeout=10) as resp:
                status_code = resp.status
                body = json.loads(resp.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

        entry = next(w for w in body if w["instance_id"] == instance_id)
        print(f"  status={status_code} step_results={entry.get('step_results')}")
        assert status_code == 200
        assert entry["step_results"] == expected

    print("\n✅ /api/workflows returns step_results unmodified")


if __name__ == "__main__":
    test_get_status_includes_step_results_matching_the_real_object()
    test_cli_workflow_status_prints_step_result_detail()
    test_cli_workflow_status_omits_detail_for_steps_with_no_result_yet()
    test_dashboard_html_renders_step_result_detail()
    test_api_workflows_passes_step_results_through_unmodified()
    print("\n" + "=" * 60)
    print("  All workflow-step-results-visibility tests passed!")
    print("=" * 60)
