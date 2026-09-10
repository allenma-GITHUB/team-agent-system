#!/usr/bin/env python3
"""
Regression test for a real crash bug found while probing main_v2.py's CLI
with malformed flags: `submit "test" --hours` (flag with no value),
`submit "test" --dept` (same), and `submit "test" --hours abc` (non-numeric
value) all raised an uncaught IndexError/ValueError and printed a raw
Python traceback instead of a clean usage message. `list --status` (no
value) had the identical hole.

Root cause: `sys.argv[sys.argv.index(flag) + 1]` assumes the flag is
never the last argument and that `float()` on its value never fails.

Fixed with a shared `_flag_value(flag)` helper in main_v2.py that bounds-
checks the index, rejects a value that is itself another flag (e.g.
`--dept --hours 5` treating "--hours" as the department), and lets
callers handle a bad `float()` conversion with a clean message instead of
letting it propagate.

Runs main_v2.main() directly with a crafted sys.argv inside an isolated
temp directory - never touches the real repo's data/ files.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

import main_v2

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


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


def _run_cli(argv):
    """Run main_v2.main() with a crafted argv, capturing stdout and any
    exception - a regression here means we caught an uncaught crash again."""
    old_argv = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = ["main_v2.py"] + argv
        with contextlib.redirect_stdout(buf):
            main_v2.main()
        return buf.getvalue(), None
    except Exception as e:  # noqa: BLE001 - deliberately catching everything
        return buf.getvalue(), e
    finally:
        sys.argv = old_argv


def test_submit_hours_with_no_value_does_not_crash():
    print_section("1. `submit ... --hours` (No Value) Doesn't Crash")
    with IsolatedCwd():
        output, exc = _run_cli(["submit", "test", "--hours"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "requires a value" in output
        assert not main_v2.TASKS_FILE.exists()


def test_submit_dept_with_no_value_does_not_crash():
    print_section("2. `submit ... --dept` (No Value) Doesn't Crash")
    with IsolatedCwd():
        output, exc = _run_cli(["submit", "test", "--dept"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "requires a value" in output
        assert not main_v2.TASKS_FILE.exists()


def test_submit_dept_immediately_followed_by_another_flag_does_not_crash():
    print_section("3. `submit ... --dept --hours 5` Doesn't Silently Misparse")
    with IsolatedCwd():
        output, exc = _run_cli(["submit", "test", "--dept", "--hours", "5"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "requires a value" in output
        assert not main_v2.TASKS_FILE.exists()


def test_submit_hours_non_numeric_does_not_crash():
    print_section("4. `submit ... --hours abc` (Non-Numeric) Doesn't Crash")
    with IsolatedCwd():
        output, exc = _run_cli(["submit", "test", "--hours", "abc"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "must be a number" in output
        assert not main_v2.TASKS_FILE.exists()


def test_list_status_with_no_value_does_not_crash():
    print_section("5. `list --status` (No Value) Doesn't Crash")
    with IsolatedCwd():
        output, exc = _run_cli(["list", "--status"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "requires a value" in output


def test_well_formed_flags_still_work():
    print_section("6. Well-Formed Flags Still Work Normally")
    with IsolatedCwd():
        output, exc = _run_cli(["submit", "a task", "--dept", "engineering", "--hours", "3"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "submitted to ENGINEERING" in output

        output, exc = _run_cli(["list", "--status", "queued"])
        print(output)
        assert exc is None, f"crashed: {exc!r}"
        assert "a task" in output


def main():
    print("\n" + "=" * 60)
    print("  CLI FLAG PARSING CRASH REGRESSION TEST")
    print("=" * 60)

    test_submit_hours_with_no_value_does_not_crash()
    test_submit_dept_with_no_value_does_not_crash()
    test_submit_dept_immediately_followed_by_another_flag_does_not_crash()
    test_submit_hours_non_numeric_does_not_crash()
    test_list_status_with_no_value_does_not_crash()
    test_well_formed_flags_still_work()

    print("\n" + "=" * 60)
    print("  [OK] All CLI flag parsing tests passed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
