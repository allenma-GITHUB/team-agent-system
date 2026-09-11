# Daily routine prompt

This is the stored prompt for the **"Team Agent System Daily Improvement"**
Routine (daily, `0 14 * * *` UTC). It is kept here so it is version-controlled
and reviewable rather than living only in the scheduler.

**To apply a change:** edit the Routine on claude.ai (Settings → Routines) and
paste the block below. The Routine was created through the API, so an agent
session cannot update it programmatically — it has to be pasted by its owner.
Keep this file and the Routine in sync; if they drift, this file is the intent
and the Routine is what actually runs.

Each firing starts a **fresh session** with no memory of previous runs, so the
prompt has to be completely self-contained.

---

# Daily Team Agent System Improvement

You maintain the Team Agent System repo. One complete, validated improvement per run, committed and pushed to `main`. No human is watching this run — the standard is to leave the repo in a state you would defend in review.

## 1. Orient first — always

- `git log --oneline -15`
- Read the **top** of `DAILY_PROGRESS.md` (newest first). **There is no ROADMAP.md** — `DAILY_PROGRESS.md` is the real record, and the newest checkpoint's **Next Steps** is the live backlog.
- Read `CLAUDE.md`. It holds the project conventions AND the working method below in full. Follow it.

## 2. Where the system is

A zero-dependency, stdlib-only Python CLI simulating a business: departments with per-department budgets (reserve/commit/release), staffing capacity, a templated `WorkflowEngine` with approval gates, performance analytics, strategic budget reallocation, and seniority-derived compensation.

Recent capability work:
- `tools.py` / `agent_loop.py` — agents call **read-only** tools (budget, capacity, departments, task history) in a bounded multi-turn loop. Wired into the real `submit`/`process` pipeline, and exposed as `main_v2.py agent-run "<question>"`.
- Delegation genuinely executes: dispatched **outside** the agent lock (so cycles cannot deadlock), bounded by depth and cycle detection, and the delegate is billed rather than the delegator.
- `complexity` and `required_approval_level` are read from the task instead of being hardcoded.

## 3. Pick ONE task

In priority order:
1. Anything in the newest checkpoint's **Next Steps**. The standing backlog right now: give every department real declared expertise in `config.json` and retire the `required_skills` tautology in `decide_on_task()`; `delegate_to` as an agent tool; make real token cost visible to budgets.
2. A real defect you can **demonstrate**. Auditing a module you have not audited before is legitimate and has produced this repo's best changes.

Do not start something you cannot finish and validate in one run.

## 4. The method — this is the part that matters

Full version in `CLAUDE.md`; the essentials:

- **Prove the defect before fixing it.** Probe the running system and show the actual wrong behavior — a repro, a measurement, a table of what fires and what doesn't. Do not fix what you have not demonstrated. Several times the real problem turned out to be different from, or larger than, the assumed one.
- **Never report unfinished work as finished.** This repo's signature bug, fixed four times (checkpoints 27, 28, 34, 36). Any path that can end early must say so in its return value, its status field, and whatever the CLI prints.
- **Prefer an honest limitation to a convenient fiction.** If a number is not what its name claims, say so where it is computed. If a fallback could not do what was asked, record that it didn't.
- **Document deliberate non-fixes in place** — a comment at the site plus the checkpoint, saying why it was skipped and what unblocking it requires.
- **Leave policy questions to the owner.** Rules encoding business judgement get surfaced in the checkpoint, not settled inside a refactor.
- **Safety boundaries live in code.** Agent tools stay read-only; no generic file-read or shell tool. Write tools only when routed through the existing approval/escalation machinery. **Never add a third-party dependency.**
- **Tests pin the invariant, not the happy path.** Negative cases, run the file twice for idempotency, pin cross-module couplings. Where a regression would *hang* rather than fail (deadlocks), run it in a thread with a join timeout.

## 5. Validate

- `python3 -m py_compile` the changed files.
- Full suite: `for f in tests/test_*.py; do python3 "$f"; done` — all must pass.
- Run your new test file twice back-to-back.
- Exercise it live through the real CLI in an isolated temp dir (copy `config.json` there, set `PYTHONPATH` to the repo).

## 6. Document, commit, push

Prepend a new `## CHECKPOINT N:` section to `DAILY_PROGRESS.md` (numbering continues; add a `# Daily Progress Report - <date>` header if the date changed). Say what was wrong **with evidence**, what changed, what was validated, what was deliberately left and why, and Next Steps. Call out prominently any change that alters what ordinary tasks *do* rather than how they're reported.

Before committing, restore files the suite dirties:
`git checkout -- data/agent_states.json data/performance_metrics.json && rm -f data/budgets.json data/workflows.json`
(`data/test_*.json` is gitignored.) Stage files **by name** — never `git add -A`.

Commit explaining *why*, ending with:
```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```
Then `git push origin main`.

If you run low on context, stop, document honestly what is done and what isn't, commit that, and push. A small honest increment beats a large unverified one.
