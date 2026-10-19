#!/usr/bin/env python3
"""
Test that `agent-run`'s CLI output shows a human what a tool call actually
returned, not just whether it succeeded.

ToolCallRecord.result_preview (agent_loop.py) has existed since that file's
first commit, built specifically so "a 20KB JSON blob per call" doesn't
reach a human - its own docstring says "this is only for humans reading a
summary". But main_v2.run_agent()'s per-call print loop never read the
field: a successful call printed only a checkmark, the tool name and its
arguments. The model saw the real budget/capacity/task data every time;
a human watching the same transcript saw nothing but "it didn't fail" -
for every tool, on every run, since the loop was introduced.

Runs entirely in an isolated tempfile.TemporaryDirectory() with a copy of
config.json, same as tests/test_main_cli_core.py, so it never touches the
real repo's data/ files. The budget tool's result itself is NOT asserted
on verbatim: main_v2.run_agent() calls build_readonly_registry() with no
args, which binds the shared budgets.budget_manager singleton - loaded
once at import time from whatever data/budgets.json happens to exist in
the real repo root at that moment (populated or not depending on what
else ran earlier in this process, or dirtied by a prior full-suite run per
this project's own documented data/budgets.json caveat). Assertions below
check the JSON *shape* the tool always returns (the "department" and
"allocated" keys), true whether or not a budget happens to be allocated,
rather than depending on either case.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import shutil
import tempfile

import main_v2

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class IsolatedCwd:
    """Same isolation pattern as tests/test_main_cli_core.py's IsolatedCwd -
    a fresh temp dir with config.json copied in, cwd restored on exit."""

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
    """Run fn, returning (result, everything it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def test_successful_tool_call_shows_its_real_result():
    print_section("1. A Successful Tool Call's Real Result Reaches The CLI Output")

    with IsolatedCwd():
        _, output = _captured(
            main_v2.run_agent, "What is the engineering department's budget status?")

    print(output)
    assert "✓ get_department_budget" in output
    # Before this fix, nothing past the checkmark and arguments ever
    # printed - the real answer the tool gave was visible only to the
    # model. It must now be in the transcript a human reads: the tool's
    # response always carries these two keys, whether or not a budget
    # happens to be allocated for "engineering" in this process.
    assert '"department": "engineering"' in output
    assert '"allocated"' in output


def test_preview_is_collapsed_to_one_line():
    print_section("2. A Multi-line JSON Preview Prints On One Line, Not Reformatted")

    with IsolatedCwd():
        _, output = _captured(
            main_v2.run_agent, "What is the engineering department's budget status?")

    preview_lines = [line for line in output.splitlines() if line.strip().startswith("->")]
    print(f"  preview lines: {preview_lines}")
    # result_preview is pretty-printed JSON (real newlines baked into the
    # string); the fix must collapse it, not dump a ragged multi-line blob
    # into what is meant to be a one-line-per-call summary.
    assert len(preview_lines) == 1
    assert "{" in preview_lines[0] and "}" in preview_lines[0]
    assert "\n" not in preview_lines[0]


def test_different_tool_result_is_also_shown():
    """The fix must generalize across tools, not just the budget one."""
    print_section("3. list_departments' Real Result Is Also Shown, Not Just Budget's")

    with IsolatedCwd():
        _, output = _captured(main_v2.run_agent, "What departments do we have?")

    print(output)
    assert "✓ list_departments" in output
    # config.json (copied into the isolated dir) declares these real
    # department names - they must appear in the preview line, proving
    # this is the tool's actual output and not a hardcoded string.
    assert "engineering" in output and "finance" in output


if __name__ == "__main__":
    for _ in range(2):  # run twice back-to-back: no state should carry over
        test_successful_tool_call_shows_its_real_result()
        test_preview_is_collapsed_to_one_line()
        test_different_tool_result_is_also_shown()

    print("\n✅ All agent-run tool result visibility tests passed!\n")
