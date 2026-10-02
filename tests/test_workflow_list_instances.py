#!/usr/bin/env python3
"""
Regression test for checkpoint 53's own named gap: `workflow status <id>`
requires already knowing an instance_id, printed exactly once at `workflow
start` time and nowhere else - there was never a way to list running
instances at all, in the CLI or over the wire. The web layer got one first
(/api/workflows, via build_workflows_payload()'s ad-hoc sort over
workflow_engine.instances.values()); this closes the CLI side with
WorkflowEngine.list_instances() as the single shared source of the
newest-first ordering, and main_v2.py's new `workflow instances` command
built on it.

Proven live before writing this test: a fresh WorkflowEngine with zero
instances printed nothing to list from, and workflow_engine.instances was a
plain dict with no ordering guarantee at all - sorting had to be newest-first
by created_at, matching what web_server.py's build_workflows_payload()
already did ad hoc.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import shutil
import tempfile
import time
from pathlib import Path

import main_v2
from workflows import WorkflowEngine, WorkflowStep, WorkflowTemplate, workflow_engine
from budgets import BudgetManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root
DATA_FILE = "data/test_workflow_list_instances.json"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _captured(fn, *args, **kwargs):
    """Run fn, returning (result, everything it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def make_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        workflow_id="wf_list_test", name="List-instances test workflow", description="",
        steps=[WorkflowStep(step_id="only_step", name="Only step", owner_department="qa_list")]
    )


class IsolatedCwd:
    """Mirrors tests/test_main_cli_core.py's own helper: runs a block inside
    a fresh temp directory with config.json copied in, then restores the
    original cwd - so main_v2's/workflow_engine's relative data/ paths stay
    isolated from the real repo."""

    def __enter__(self):
        self._original_cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        shutil.copy(os.path.join(REPO_ROOT, "config.json"), self._tmpdir)
        os.chdir(self._tmpdir)
        return self._tmpdir

    def __exit__(self, *exc):
        os.chdir(self._original_cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)


def test_list_instances_is_empty_on_a_fresh_engine():
    print_section("1. list_instances() On A Fresh Engine Is An Empty List, Not An Error")

    Path(DATA_FILE).unlink(missing_ok=True)
    engine = WorkflowEngine(data_file=DATA_FILE)
    result = engine.list_instances()
    print(f"  list_instances(): {result}")
    assert result == []

    Path(DATA_FILE).unlink(missing_ok=True)


def test_list_instances_returns_every_instance_newest_first():
    print_section("2. list_instances() Returns Every Instance, Newest First")

    Path(DATA_FILE).unlink(missing_ok=True)
    budgets = BudgetManager(data_file="data/test_workflow_list_instances_budgets.json")
    engine = WorkflowEngine(data_file=DATA_FILE, budget_manager=budgets)
    engine.register_template(make_template())

    first = engine.create_instance("wf_list_test", {"title": "First"})
    time.sleep(0.01)  # created_at has microsecond precision - force a real gap
    second = engine.create_instance("wf_list_test", {"title": "Second"})
    time.sleep(0.01)
    third = engine.create_instance("wf_list_test", {"title": "Third"})

    instances = engine.list_instances()
    ids = [i.instance_id for i in instances]
    print(f"  Creation order: {[first.instance_id, second.instance_id, third.instance_id]}")
    print(f"  list_instances() order: {ids}")

    assert len(instances) == 3
    assert ids == [third.instance_id, second.instance_id, first.instance_id]

    Path(DATA_FILE).unlink(missing_ok=True)
    Path("data/test_workflow_list_instances_budgets.json").unlink(missing_ok=True)


def test_cli_workflow_instances_reports_no_instances_cleanly():
    print_section("3. CLI `workflow instances` With Nothing Started")

    with IsolatedCwd():
        main_v2.init_workflows()
        # Isolated cwd, but workflow_engine is still the shared global
        # singleton (same caveat tests/test_web_server.py documents) - a
        # prior test in this same run may have left instances behind, so
        # this only pins the empty-case message format, not an exact count.
        if not workflow_engine.instances:
            _, output = _captured(main_v2.workflow_instances)
            print(output)
            assert "No workflow instances." in output
            assert "workflow start" in output


def test_cli_workflow_instances_lists_a_real_started_instance():
    print_section("4. CLI `workflow instances` Lists A Real Started Instance")

    with IsolatedCwd():
        main_v2.init_workflows()
        instance = workflow_engine.create_instance("bug_fix", {"title": "Crash on save"})
        workflow_engine.start_instance(instance.instance_id)

        _, output = _captured(main_v2.workflow_instances)
        print(output)

        assert instance.instance_id in output
        assert "Bug Fix Workflow" in output
        assert "in_progress" in output


def main():
    print("\n" + "=" * 60)
    print("  WORKFLOW list_instances() / `workflow instances` CLI TESTS")
    print("=" * 60)

    test_list_instances_is_empty_on_a_fresh_engine()
    test_list_instances_returns_every_instance_newest_first()
    test_cli_workflow_instances_reports_no_instances_cleanly()
    test_cli_workflow_instances_lists_a_real_started_instance()

    print("\n" + "=" * 60)
    print("  [OK] All workflow-list-instances tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
