#!/usr/bin/env python3
"""
WorkflowInstance carries three timestamp fields - created_at, started_at,
completed_at (workflows.py ~line 119-121) - all three persisted to disk on
every save (workflows.py's to_dict()/from_dict() round-trip them). But
get_status() (the one dict every human-facing consumer reads: main_v2.py's
`workflow status <id>` CLI command, static/dashboard.html's workflow cards,
and web_server.py's /api/workflows route, which returns get_status()'s
dict unmodified) only ever returned created_at and completed_at -
started_at was silently dropped, the only one of the three missing from
the dict itself. Neither the CLI nor the dashboard printed any of the
three timestamps get_status() did supply, either.

Confirmed live before this fix: starting a real instance set a real,
non-null started_at on the WorkflowInstance object, but
engine.get_status(instance_id) had no "started_at" key at all, and
main_v2.workflow_status()'s captured stdout had no "Created"/"Started"/
"Completed" line anywhere in it.

Fixed: workflows.py's get_status() now includes started_at alongside the
two timestamps it already had. main_v2.py's workflow_status() and
static/dashboard.html's loadWorkflows() each gained a line rendering all
three - same escapeHtml()-guarded, "no value yet" convention every other
optional field in both files already follows (e.g. task.created_at on the
task-detail panel).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import shutil
import tempfile

import main_v2
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate

DATA_FILE = "data/test_workflow_timestamps.json"
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


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_ts_test", name="Timestamp test workflow", description="",
        steps=[
            WorkflowStep(step_id="intake", name="Intake", owner_department="qa_ts"),
        ]
    )


def _cleanup():
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)


def test_get_status_includes_started_at():
    print_section("1. get_status() Carries started_at, Not Just created_at/completed_at")
    _cleanup()
    try:
        engine = WorkflowEngine(data_file=DATA_FILE)
        engine.register_template(make_template())
        instance = engine.create_instance("wf_ts_test", {})
        engine.start_instance(instance.instance_id)

        print(f"  real object: created_at={instance.created_at!r} "
              f"started_at={instance.started_at!r} completed_at={instance.completed_at!r}")
        assert instance.started_at is not None, "start_instance() should have set a real started_at"

        status = engine.get_status(instance.instance_id)
        print(f"  get_status() keys: {sorted(status.keys())}")
        assert "started_at" in status, "get_status() dropped started_at entirely"
        assert status["started_at"] == instance.started_at
        assert status["created_at"] == instance.created_at
        assert status["completed_at"] == instance.completed_at
    finally:
        _cleanup()

    print("\n✅ get_status() includes started_at")


def test_cli_workflow_status_prints_all_three_timestamps():
    print_section("2. CLI `workflow status` Prints Created/Started/Completed")

    with IsolatedCwd():
        instance_id = main_v2.workflow_start("bug_fix", ["severity=high"])
        instance = main_v2.workflow_engine.get_instance(instance_id)
        assert instance.started_at is not None

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main_v2.workflow_status(instance_id)
        output = buf.getvalue()
        print(output)

        assert "Created:" in output
        assert "Started:" in output
        assert "Completed:" in output
        assert instance.created_at in output
        assert instance.started_at in output
        assert "(not completed)" in output

    print("\n✅ CLI workflow status prints all three timestamps")


def test_dashboard_html_renders_workflow_timestamps():
    print_section("3. Dashboard HTML Renders Workflow Timestamps")
    with open(os.path.join(REPO_ROOT, "static", "dashboard.html"), encoding="utf-8") as f:
        body = f.read()

    script_section = body.split("async function loadWorkflows")[-1].split("async function postWorkflow")[0]
    print(f"  loadWorkflows() section length: {len(script_section)} chars")

    assert "w.created_at" in script_section
    assert "w.started_at" in script_section
    assert "w.completed_at" in script_section
    assert "escapeHtml(w.created_at" in script_section
    assert "escapeHtml(w.started_at" in script_section
    assert "escapeHtml(w.completed_at" in script_section

    print("\n✅ Dashboard renders created_at/started_at/completed_at")


if __name__ == "__main__":
    test_get_status_includes_started_at()
    test_cli_workflow_status_prints_all_three_timestamps()
    test_dashboard_html_renders_workflow_timestamps()
    print("\n" + "=" * 60)
    print("  All workflow-timestamp-visibility tests passed!")
    print("=" * 60)
