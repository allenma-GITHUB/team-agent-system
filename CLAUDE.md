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
- Tests inject isolated managers pointed at `data/test_*.json` (gitignored)
  so they never touch the real data files. `AgentRegistry.register()`
  returns an *existing* state if a prior run left one on disk and metrics
  accumulate across runs, so reset any state you assert on absolutely
  (`state.metrics = PerformanceMetrics()`, `current_workload`,
  `learned_preferences`) rather than assuming a clean file.
- Running the suite still dirties tracked seed files
  (`data/agent_states.json`, `data/performance_metrics.json`) and creates
  `data/budgets.json` / `data/workflows.json`. Restore and remove those
  before committing: `git checkout -- data/agent_states.json
  data/performance_metrics.json && rm -f data/budgets.json data/workflows.json`.

# How this project is built

**Zero third-party dependencies.** Standard library only, everywhere. This
is a deliberate constraint, not an accident - the repo is maintained partly
by an unattended daily agent, and every dependency is ongoing maintenance
burden carried by automation that cannot exercise judgement about a CVE.
Adding one needs an explicit decision from the owner.

**One complete, validated increment per session.** Not half a feature.
Something that would survive review on its own.

**Prove the defect before fixing it.** Probe the running system and show the
actual wrong behavior - a repro, a direct measurement, a table of what fires
and what doesn't - before writing a line of fix. Several of this repo's most
valuable changes came from discovering the real problem was different from,
or larger than, the assumed one. Do not fix what you have not demonstrated.

**Never report unfinished work as finished.** This codebase's signature bug,
found and fixed four separate times: budget escalations recorded as
"completed" (cp 27), workflow steps marked done when execution escalated
(cp 28), a tool loop that would have returned a half-formed answer as a
conclusion (cp 34), an agent exhausting its iteration budget (cp 36). Any
new code path that can end early must say so in its return value, its status
field, and whatever the CLI prints. When in doubt, escalate loudly.

**Prefer an honest limitation to a convenient fiction.** If a number is not
what its name claims, say so where it is computed (`quality_score` measures
execution health, not quality). If a fallback could not do what was asked,
record that it didn't (`used_tools`, `delegation_declined`) rather than
letting a report imply something that never happened.

**Document deliberate non-fixes in place.** When you find a defect and
choose not to fix it this session, write down where it is, why it was
skipped, and what unblocking it requires - in a comment at the site, and in
the checkpoint. A known tautology with a stated reason is useful; a silently
skipped one gets rediscovered from scratch.

**Leave policy questions to the owner.** Thresholds and rules that encode
business judgement - whether junior agents need sign-off on routine work,
what a budget should cover - are decisions to surface in the checkpoint, not
to settle inside a refactor.

**Safety boundaries are enforced in code, not convention.** Agent tools are
read-only: `ToolRegistry` refuses to register a `mutates=True` tool and
re-checks at execute time. There is deliberately no generic file-read or
shell tool - "read-only" is not "safe", and an agent that can read any path
can read `.env` into a prompt that gets persisted to `tasks.json`. Write
tools arrive only when routed through the existing approval/escalation
machinery.

**Tests pin the invariant, not the happy path.** Cover the negative cases
(malformed input, a hallucinated tool name, a handler that throws), run the
file twice back-to-back to prove idempotency, and pin couplings that span
modules and would otherwise break silently - e.g. the complexity tiers in
`departments.py` must clear the thresholds `agent_decisions.py` compares
them against. Where a regression would *hang* rather than fail an assertion
(deadlocks), run the scenario in a thread with a join timeout so the timeout
is the assertion.

**Say when behavior changes.** A change that alters what ordinary tasks do -
not just how they're reported - gets called out prominently in the
checkpoint. Refactors and behavior changes are different things.

# Where the record lives

There is no `ROADMAP.md`. **`DAILY_PROGRESS.md` is the real record**, newest
first: a `# Daily Progress Report - <date>` header, then `## CHECKPOINT N:`
sections prepended above the previous one, numbering continuing across days.
Each entry says what was wrong (with evidence), what changed, what was
validated, what was deliberately left, and what comes next. The most recent
checkpoint's **Next Steps** is the live backlog.
