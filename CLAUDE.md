# Project conventions

- **All test files live in `tests/`.** Every `test_*.py` file belongs in
  this directory, not the repo root - keep it that way when adding new
  tests. Run one directly with `python3 tests/test_whatever.py` from the
  repo root, or the whole suite with
  `for f in tests/test_*.py; do python3 "$f"; done`.
- Each test file starts with a small `sys.path` bootstrap, right after its
  module docstring and before any other imports:
  ```python
  import os
  import sys
  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
  ```
  This lets `import budgets`, `import main_v2`, etc. resolve regardless of
  the current working directory or how the test is invoked. Copy it from
  any existing file in `tests/` when adding a new one.
- A test that needs the real `config.json` (e.g. to run `main_v2.py`'s CLI
  in an isolated temp directory) computes its repo root as
  `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` (two levels
  up from `tests/test_x.py`), not one.
