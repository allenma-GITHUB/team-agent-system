#!/usr/bin/env python3
"""
`agent-run`'s CLI output can silently cut off a tool's real result mid-word,
with no sign to a human reading it that anything is missing.

ToolCallRecord.result_preview (agent_loop.py) is sliced to
RESULT_PREVIEW_CHARS (200) at the point it's built - by the time it reaches
main_v2.run_agent()'s print loop, the rest of the string is simply gone;
there is no way to recover it from the record. For a tool like list_tasks,
whose JSON grows with every task on file, that preview routinely ends with
no closing brace and a word sliced in half - and until this fix, nothing
told the human reading it that the line was a fragment rather than the
whole answer. The model making the decision always saw the complete
result; the human watching over its shoulder saw less than it did,
indistinguishably from a tool returning malformed JSON.

The complete, untruncated content a tool actually returned is not lost -
it survives in AgentRunResult.messages' "tool"-role entries (same object
the model's next turn is built from), which had zero readers anywhere in
this codebase (confirmed by `grep -rn "\\.messages\\b" --include=*.py
--include=*.html .` outside agent_loop.py and its own declaration) before
this fix. run_agent() now has a `--full-output` flag that prints that
field instead of the truncated preview, and marks the default preview as
truncated whenever the real content was longer than what's shown.

Runs entirely in an isolated tempfile.TemporaryDirectory() with a copy of
config.json, same pattern as tests/test_agent_run_tool_result_visibility.py,
so it never touches the real repo's data/ files.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import shutil
import tempfile

import main_v2
from agent_loop import RESULT_PREVIEW_CHARS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tests/ -> repo root

# One description alone is short; eight of them together in one list_tasks
# JSON blob reliably pushes the result past RESULT_PREVIEW_CHARS (200).
LONG_DESCRIPTION = (
    "A reasonably long task description so the JSON blob grows past two "
    "hundred characters once several of these are listed together in one "
    "tool result"
)


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class IsolatedCwd:
    """Same isolation pattern as tests/test_agent_run_tool_result_visibility.py -
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


def _seed_long_task_history(count: int = 8):
    """Submit and process enough tasks that list_tasks' JSON result is
    guaranteed to exceed RESULT_PREVIEW_CHARS."""
    for i in range(count):
        main_v2.submit_task(f"{LONG_DESCRIPTION} (task {i})", department="engineering")
    main_v2.process_tasks(parallel=False)


def test_truncated_preview_is_marked():
    print_section("1. A Truncated Preview Says So, Instead Of Reading As Complete")

    with IsolatedCwd():
        _seed_long_task_history()
        _, output = _captured(main_v2.run_agent, "What tasks has engineering worked on?")

    print(output)
    assert "✓ list_tasks" in output
    # Before this fix, a result longer than the preview just stopped mid-
    # word with no indication - indistinguishable from the tool returning
    # malformed JSON. It must now say so explicitly.
    assert "(truncated - re-run with --full-output to see all of it)" in output


def test_full_output_shows_untruncated_content():
    print_section("2. --full-output Recovers The Rest Of The Real Result")

    with IsolatedCwd():
        _seed_long_task_history()
        _, output = _captured(
            main_v2.run_agent, "What tasks has engineering worked on?", full_output=True)

    print(output)
    assert "✓ list_tasks" in output
    # With 8 tasks seeded, the real list_tasks JSON is unambiguously longer
    # than the 200-char preview - the full-output path must actually print
    # more of it, not just repeat the same truncated fragment.
    tool_call_section = output.split("Tool calls:")[1].split("\n\n")[0]
    assert len(tool_call_section) > RESULT_PREVIEW_CHARS
    # The 8th (last) task's description only appears past character 200 of
    # the raw JSON - its presence proves this is the real, complete content
    # and not a longer-but-still-partial slice.
    assert "(task 7)" in output
    # --full-output is the opposite of a truncated fragment: it must not
    # also claim to be truncated.
    assert "(truncated" not in output


def test_short_result_is_not_falsely_marked_truncated():
    print_section("3. A Short Result Is Never Marked Truncated")

    with IsolatedCwd():
        _, output = _captured(main_v2.run_agent, "What departments do we have?")

    print(output)
    assert "✓ list_departments" in output
    # list_departments' real result is well under 200 chars for this
    # project's config.json - the marker must only ever appear when content
    # was actually cut off, never as a blanket disclaimer on every call.
    assert "(truncated" not in output


def test_full_output_cli_flag_reaches_run_agent():
    print_section("4. The --full-output CLI Flag Actually Flows Through main()")

    with IsolatedCwd():
        _seed_long_task_history()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sys.argv = ["main_v2.py", "agent-run", "What tasks has engineering worked on?",
                        "--full-output"]
            main_v2.main()
        output = buf.getvalue()

    print(output)
    assert "(task 7)" in output
    assert "(truncated" not in output


if __name__ == "__main__":
    for _ in range(2):  # run twice back-to-back: no state should carry over
        test_truncated_preview_is_marked()
        test_full_output_shows_untruncated_content()
        test_short_result_is_not_falsely_marked_truncated()
        test_full_output_cli_flag_reaches_run_agent()

    print("\n✅ All agent-run full-output tests passed!\n")
