# Daily Progress Report - September 10, 2026

## 🐛 CHECKPOINT 25: NEGATIVE `--hours` COULD MANUFACTURE BUDGET OUT OF THIN AIR

**Found while probing `main_v2.py`'s CLI with edge-case inputs** (a new review angle after several checkpoints of concurrency-bug hunting): `submit "test" --dept engineering --hours -5` was accepted silently — `✓ Task 0001 submitted to ENGINEERING (est. -5.0h)`.

**Why this mattered, not just cosmetic:** that `-5.0` flows straight into `task_executor_v2.py`'s budget check as `amount = estimated_hours * self.cost_per_hour` (a negative number), which reaches `BudgetManager.request_expense()`. `DepartmentBudget.can_afford()` is `amount <= available()`, which any negative amount trivially satisfies, and then `budget.spent += amount` *decreases* recorded spend instead of increasing it. Submitting enough negative-hours tasks could drive a department's `spent` arbitrarily negative — manufacturing budget capacity rather than consuming it, and corrupting every downstream utilization/report figure built on `spent`. `BudgetManager.reserve_funds()` → `DepartmentBudget.reserve()` had the identical hole on the reservation path used by workflow approval gates.

### ✅ Fix — three layers, innermost is the one that actually matters

1. **`budgets.py` (the real fix)** — `BudgetManager.request_expense()` and `reserve_funds()` now reject `amount < 0` outright, before touching any budget, regardless of what fed them the bad number. This is the actual money-mutation boundary, so it's the layer that protects the invariant no matter which caller (CLI today, anything else tomorrow) gets it wrong.
2. **`main_v2.py` `submit_task()`** — rejects a negative `estimated_hours` up front with a clear `✗ estimated_hours must be >= 0, got X` message and returns `None` instead of queuing the task, so any direct caller (not just the CLI) is covered.
3. **`main_v2.py` CLI `--hours` parsing** — rejects a negative value immediately with a usage-style error, before ever calling `submit_task()`, for the fastest/clearest feedback at the actual point of bad input.

Zero hours remains valid (a legitimately free/instant task) — only negative values are rejected.

**Also checked, decided not to fix:** `submit ""` (empty description) is accepted and routed to the default department with a 1.0h estimate. Doesn't corrupt any state — just a minor UX gap, not a bug — so left alone rather than adding validation nothing actually needs.

**New test:** `test_negative_hours_validation.py` — covers `submit_task()` rejecting negative hours (task not queued), zero/positive hours still working, the CLI `--hours -5` path being rejected before any file write, and `BudgetManager.request_expense()`/`reserve_funds()` rejecting negative amounts directly with `spent`/`reserved` left untouched. Run twice back-to-back to confirm no accumulation; full 23-file test suite (all pre-existing tests plus this one) re-run clean afterward. Verified live via the real CLI in an isolated temp dir: `--hours -5` is rejected and never appears in `list`, while `--hours 0` and `--hours 5` both queue normally.

---

## 🎯 PHASE 3 FOLLOW-UP: WIRE BUDGETS & CAPACITY INTO TASK EXECUTION

**Summary:** Yesterday's `budgets.py` was a standalone, tested module — nothing in the execution path actually called it, and `DepartmentHeadAgent.decide_on_task()` (from Phase 1) was defined but never invoked from `run()`. Today closes that gap: department heads now check capacity, run the decision engine, and clear a real budget check *before* doing (and billing) any work, instead of executing unconditionally.

### ✅ What Changed

**`task_executor_v2.py` — `DepartmentHeadAgent`**
- `run()` now, before touching the LLM:
  1. Takes a `capacity_manager.snapshot()` of the department and emits it as a `capacity_check` event (informational for now — no cross-department rerouting yet, but a stretched-thin department now shows up in traces)
  2. Calls `decide_on_task()` (previously defined, never called) and short-circuits to an `escalated` result — no LLM call, no spend — if the decision engine can't find anyone to do the work
  3. If the decision requires approval, calls `budget_manager.approve_decision()` (the bridge added yesterday) against the department's real budget; a denial escalates the same way, without spending anything
- Added `cost_per_hour` (default $100) as a constructor param so the estimated cost of approval-requiring work is configurable per agent
- `decide_on_task()` now also returns the raw `agent_decisions.DecisionResult` (`raw_decision` key) so `run()` can feed it straight into `budget_manager.approve_decision()`
- Constructor now calls `budget_manager.ensure_allocated(department, ...)` to seed a starting budget from `config.json` on first use

**`departments.py`**
- Added `DepartmentManager.get_monthly_budget(department)`, reading the `monthly_budget` field added to `config.json` yesterday (falls back to $10,000 if unset), mirroring the existing `get_staff_count()`

**`budgets.py`**
- Added `BudgetManager.ensure_allocated()`: allocates a starting budget only if the department doesn't have one yet, so re-running the system doesn't reset a period's tracked spend every time an agent is constructed

**`test_resource_gating.py` (new)** — four scenarios, all passing:
1. A normal short task executes without needing approval (no budget check happens at all)
2. Every run emits a `capacity_check` event with department utilization
3. A task big enough to require approval, against a healthy budget, executes and the budget shows the spend
4. The same task against a budget that can't afford it: escalates, `$0` spent, no LLM call made — the gate actually blocks work rather than just logging a warning

### 🔧 Design Decisions

- **Escalation is a real short-circuit, not a warning.** Both the "no available delegate" and "budget denied" paths return before the workload counter is incremented or the LLM is called — the point of yesterday's budget module was to make spend preventable, not just visible after the fact.
- **Approval-gated cost only, for now.** Budget is only drawn on when `decision.approval_required` is true (large/complex tasks). Routine tasks still run for free in this model. Metering the actual per-task LLM cost against budget regardless of approval level is a reasonable next step but a separate change — didn't fold it in to keep today's diff reviewable.
- **Capacity is observational only.** `capacity_check` is emitted every run but nothing yet reroutes work away from an over-capacity department — there's no cross-department task handoff in `TaskExecutor` to reroute *to*. Recording it now means the data needed for that decision already exists in traces once handoff exists.
- **Test data hygiene.** `test_resource_gating.py` uses fresh `qa_*` department names via the *shared* `agent_state_registry`/`budget_manager` singletons (since `DepartmentHeadAgent` doesn't take a registry override), so after running it I reverted `data/agent_states.json` to HEAD and deleted the freshly-created `data/budgets.json` rather than committing test fixtures into shared state.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files
- `test_resource_gating.py`: all 4 scenarios pass
- Re-ran `test_agent_decisions.py`, `test_performance.py`, `test_workflows.py`, `test_budgets.py`: all still pass
- Re-ran `test_resource_gating.py` a second time from a fully cleaned state to confirm it's reproducible, not order-dependent on leftover files

### 📝 Next Steps

- Meter actual LLM/labor cost against budget on every task, not just approval-gated ones
- Use an over-capacity `capacity_check` to actually redirect work (once `TaskExecutor` supports cross-department handoff) instead of just logging it
- Wire the same `reserve_funds()`/`confirm_reservation()` flow into `workflows.py`'s existing "Budget Approval" step
- Give `DepartmentHeadAgent` an injectable registry (like `test_budgets.py`'s isolated `AgentRegistry`) so integration tests don't have to touch shared state at all

### 📂 Files Modified

- `task_executor_v2.py` (capacity/decision/budget gating in `DepartmentHeadAgent.run()`)
- `departments.py` (added `get_monthly_budget()`)
- `budgets.py` (added `BudgetManager.ensure_allocated()`)
- `test_resource_gating.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 SAME-DAY FOLLOW-UP: BUDGET-GATED WORKFLOW APPROVAL STEPS

**Summary:** Second checkpoint today. The task-execution gap above is closed, but `workflows.py`'s "Budget Approval" step was still just a label — `approve_step()` never touched money, and `StepStatus.BLOCKED`/`WorkflowStatus.ESCALATED` were defined in the enums but never actually assigned anywhere. Wired the same `budgets.py` reserve/confirm/cancel flow into the workflow engine so an approval-gated step with a real cost can't silently proceed past a department that can't afford it.

### ✅ What Changed

**`workflows.py`**
- `WorkflowStep` gains `estimated_cost` (default `0.0`, so existing approval steps with no cost are unaffected) and `budget_department` (defaults to `owner_department` via new `budget_dept()` helper) — lets a step be *owned* by one department (e.g. `finance`, who approves) while its cost is funded from another's budget (e.g. `engineering`, who actually pays for the work)
- `WorkflowEngine.get_next_step()`: before advancing into a step that `requires_approval` and has `estimated_cost > 0`, it seeds the department's budget from `config.json` (`budget_manager.ensure_allocated`, same helper `task_executor_v2.py` uses) and calls `reserve_funds()`. If the department can't afford it, the step is marked `BLOCKED` and the workflow `ESCALATED` — both enum values existed since Phase 2 but were dead code until now — and `get_next_step()` returns `None` instead of handing back a step no one can fund
- `WorkflowEngine.approve_step()`: on approval, `confirm_reservation()` converts the hold into real logged spend; on rejection, `cancel_reservation()` releases it back to the department instead of leaving it stuck as a phantom hold
- `create_feature_request_workflow()`: gave the "Design Specification" step a real `$3,000` cost against `design`'s budget, and "Budget Approval" a `$15,000` cost that's owned by `finance` but funded from `engineering`'s budget — a concrete example of the owner/funder split

**`test_workflow_budget.py` (new)** — four scenarios, all passing:
1. An affordable step reserves on advance, then approval commits the reservation to spend
2. A rejected step releases its reservation rather than spending it
3. A step costing more than the department can afford blocks (`StepStatus.BLOCKED`) and escalates the workflow (`WorkflowStatus.ESCALATED`) without touching the budget at all
4. A step owned by one department but funded by another's budget reserves/spends against the *funding* department, not the owner

### 🔧 Design Decisions

- **Reservation happens on advance, not on approval.** If the hold were only taken at `approve_step()` time, two different steps (or a step and some other spend) could both "pass" a check against the same uncommitted dollars in the window between a step starting and being approved — the exact race yesterday's `DepartmentBudget.reserved` was built to prevent. Reserving as soon as the engine hands out the step closes that window here too.
- **Backward compatible by construction.** `estimated_cost` defaults to `0.0`, so every existing workflow step (including "Engineering Estimate," "Implementation," "Launch," and the entire `bug_fix` template) is completely untouched — the existing `test_workflows.py` suite runs unmodified and unaffected, it just now also does a real `$3,000` reserve/confirm cycle against `design`'s budget under the hood for its "Design Specification" step.
- **Owner vs. funder is a real distinction, not just plumbing.** A CEO/finance sign-off step approves spend, it doesn't source it — modeling `budget_department` separately means the reservation lands on the department whose money actually leaves, which is what an org chart looks like in practice.
- **No auto-retry on BLOCKED.** A blocked step stays blocked; nothing currently re-attempts it once budget frees up. That's an intentional scope cut, not an oversight — see Next Steps.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files
- `test_workflow_budget.py`: all 4 scenarios pass
- Re-ran the full suite (`test_agent_decisions.py`, `test_performance.py`, `test_workflows.py`, `test_budgets.py`, `test_resource_gating.py`, `test_workflow_budget.py`): all still pass, including `test_workflows.py`'s existing walk through the now-budget-gated "Design Specification" step
- Re-ran `test_workflow_budget.py` again from a fully cleaned state to confirm reproducibility

### 📝 Next Steps

- A `resume_step()`/retry path for `BLOCKED` steps once budget frees up (currently they stay stuck)
- Give the `bug_fix` workflow's "Verification" step a real cost too, now that the plumbing exists
- Surface `WorkflowStatus.ESCALATED` instances somewhere a human/CEO agent would actually see them (today it's just instance state, no notification)
- The cross-cutting item from yesterday still stands: meter actual per-task LLM/labor cost against budget, not just approval-gated estimates

### 📂 Files Modified

- `workflows.py` (budget reserve/confirm/cancel wired into `get_next_step()`/`approve_step()`, new `WorkflowStep` fields)
- `test_workflow_budget.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 THIRD CHECKPOINT TODAY: ROUTINE TASKS ARE NO LONGER FREE

**Summary:** Both checkpoints above only drew from the budget on the *approval-required* path — a routine task with `estimated_hours` under the ~16h/complexity threshold ran for free, no matter how many of them a department processed. That understated cost and meant a department's budget could never actually run out from ordinary work, only from big one-off asks. Closed that.

### ✅ What Changed

**`task_executor_v2.py`**
- `DepartmentHeadAgent.run()`: when a decision does *not* require approval, it now calls `budget_manager.request_expense()` directly for `estimated_hours * cost_per_hour`, the same way the approval path already called `approve_decision()`. Both paths now emit the same `budget_check` event (with an `approval_required` flag added so traces can tell them apart) and escalate the same way on denial.
- Net effect: **every** executed task costs something and can be blocked by a fully depleted budget, not just the large/complex ones.

**`test_resource_gating.py`**
- Renamed/updated scenario 1 to assert the (small) spend that now happens even without approval, and that it emits exactly one `budget_check` event with `approval_required: False`
- Added scenario 5: a department with less money than a single hour of labor costs escalates a routine task with `$0` spent — the gate blocks *before* work starts, it doesn't run first and fail to bill after
- Fixed a real bug this surfaced: scenario 1 originally didn't call `budget_manager.allocate()` before running, so on a second local run it read the department's already-spent budget back from `data/budgets.json` and the hardcoded `assert budget.spent == 100` failed — spend had accumulated to `$200`. All budget-asserting scenarios now explicitly `allocate()` a fresh starting budget so they're safe to run repeatedly against the persisted (and, day-to-day, intentionally *not* reset) global `budget_manager`.

### 🔧 Design Decisions

- **Same event, one new field, not a second event type.** Adding `approval_required` to the existing `budget_check` payload keeps a single place to look at trace time instead of two similar-but-different event types for what's conceptually one check.
- **This test bug is a real lesson about the shared singleton.** `budget_manager`/`agent_state_registry` persist to disk and are shared across every process in this repo (by design — it's how the system remembers spend/relationships between CLI invocations). Any test that asserts an absolute spend value against a department name must `allocate()` it first; asserting deltas or using an `ensure_allocated`-seeded fresh name isn't enough on its own if a previous run already touched that name.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files
- `test_resource_gating.py` run twice back-to-back from the same un-reset `data/budgets.json` (i.e. the realistic case) to confirm the fix actually makes it idempotent, not just clean-slate-passing
- Full suite (`test_agent_decisions.py`, `test_performance.py`, `test_workflows.py`, `test_budgets.py`, `test_resource_gating.py`, `test_workflow_budget.py`): all pass

### 📝 Next Steps

- A `resume_step()`/retry path for `BLOCKED` workflow steps once budget frees up
- Give the `bug_fix` workflow's "Verification" step a real cost too
- Surface `WorkflowStatus.ESCALATED` / a depleted department budget somewhere a human would actually see it (today it's only in-process state and trace events)
- Add a spend/capacity view to `performance.generate_report()` so one report covers quality, cost, and resource utilization together

### 📂 Files Modified

- `task_executor_v2.py` (non-approval tasks now draw from budget too)
- `test_resource_gating.py` (new scenario + accumulation-bug fix)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 FOURTH CHECKPOINT TODAY: BUDGET & CAPACITY IN THE PERFORMANCE REPORT

**Summary:** Quality, cost, budget, and capacity have been three separate, unconnected stories all day: `performance.generate_report()` only ever showed quality/cost/success metrics, with no visibility into what any of it actually cost against a real budget or how staffed each department was. This was called out as a next step in both of today's earlier checkpoints. Closed it.

### ✅ What Changed

**`performance.py`**
- Added `PerformanceAnalytics.get_resource_summary(budgets=None, capacity=None)`: pulls `organization_summary()` and `over_budget_departments()` from a `BudgetManager`, and `organization_utilization()`/`recommend_actions()` from a `CapacityManager`. Defaults to the shared global `budget_manager`/`capacity_manager` (the same singletons `task_executor_v2.py` and `workflows.py` already draw from), but takes injected instances too.
- `generate_report()` now takes the same optional `budgets`/`capacity` params and appends a "Resource Overview" section (allocated/spent/reserved/available, capacity utilization, an over-budget-departments flag, and any capacity recommendations) — but only when something has actually been allocated, so a fresh system's report doesn't show an empty, confusing section.

**`test_performance_resources.py` (new)** — three scenarios, all passing:
1. `get_resource_summary()` reflects whatever `BudgetManager`/`CapacityManager` it's given (injected, not the global singletons)
2. `generate_report()` renders the resource section, including the over-budget flag, when a department has real spend
3. The section is cleanly omitted when nothing's been allocated yet, while the rest of the report still renders

### 🔧 Design Decisions

- **Optional injection, not a required dependency.** `get_resource_summary()`/`generate_report()` default to the global singletons so existing callers (`analytics.generate_report()` with no args) don't change behavior, but accept overrides so tests don't have to touch shared state — the exact "injectable dependency" gap flagged as a next step after the very first `budgets.py` checkpoint two days ago, generalized here instead of only fixed for `DepartmentHeadAgent`.
- **Fully isolated test, this time.** Every earlier test today used the shared global registries/managers for at least one department and needed manual cleanup (`git checkout -- data/agent_states.json`, deleting stray `data/budgets.json`) afterward. `test_performance_resources.py` is the first one that touches *zero* shared state — three isolated `data_file`s for `BudgetManager`, `AgentRegistry`, and `PerformanceAnalytics` each, verified idempotent by running it twice back-to-back with no cleanup in between.
- **Guarded, not unconditional.** The section only appears once `total_allocated > 0` — showing "$0.00 allocated, 0% utilized" on every report before anyone's configured budgets would just be noise.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files; `import performance` confirmed no circular import with `budgets.py`
- `test_performance_resources.py`: all 3 scenarios pass, run twice back-to-back with no cleanup between runs (fully isolated, so no accumulation possible)
- Full suite (`test_agent_decisions.py`, `test_performance.py`, `test_workflows.py`, `test_budgets.py`, `test_resource_gating.py`, `test_workflow_budget.py`, `test_performance_resources.py`): all pass

### 📝 Next Steps

- Apply the same injectable-dependency pattern to `DepartmentHeadAgent` (registry) so `test_resource_gating.py` and `test_workflow_budget.py` can stop touching shared state too
- A `resume_step()`/retry path for `BLOCKED` workflow steps once budget frees up
- Give the `bug_fix` workflow's "Verification" step a real cost too
- Wire `get_resource_summary()`/the new report section into `main_v2.py`'s CLI `status` command so it's reachable without writing a script

### 📂 Files Modified

- `performance.py` (`get_resource_summary()`, resource section in `generate_report()`)
- `test_performance_resources.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 FIFTH CHECKPOINT TODAY: CLI ACCESS + A REAL ANALYTICS-WIRING GAP

**Summary:** Everything built today lived behind test scripts - there was no way to see budget, capacity, or the new resource report through the actual CLI (`main_v2.py`). Wiring that in surfaced a second, more fundamental gap while validating it end-to-end: `performance.analytics.record_task()` - the whole point of Phase 4 - was defined but **never called from anywhere**. Running real tasks through the CLI updated `agent_state`'s own per-agent metrics but never told `PerformanceAnalytics` a single task had happened, so `report`'s "Total Tasks" was always `0` no matter how much work ran. Fixed both.

### ✅ What Changed

**`main_v2.py`**
- `show_status()` now prints a "Budget & Capacity" section per department (spent/allocated, utilization, capacity slots used) plus an over-budget flag, instead of only ever showing task counts
- Added a `report` command that prints `performance.analytics.generate_report()` - the single quality+cost+budget+capacity view built earlier today, now reachable without writing a script

**`task_executor_v2.py`**
- `DepartmentHeadAgent.run()` now calls `analytics.record_task()` right alongside the existing `self.agent_state.record_performance()` call, using the same quality/duration/cost/success values. An escalated task (no work done) still correctly records nothing.

**`test_analytics_wiring.py` (new)** — three scenarios, all passing and idempotent (verified by running twice back-to-back):
1. A completed task increments that agent's `AgentMetrics.tasks_completed` in `performance.analytics`
2. An escalated task (budget denied) records nothing - no phantom "completed" work for something that never ran
3. `generate_report()`'s System Overview reflects tasks recorded this way

### 🔧 How This Was Found

Validating the new `report` CLI command end-to-end (not just unit tests) against a temp working directory: after `submit` + `process`-ing two real tasks, `report` showed `Total Tasks: 0`. That's the kind of gap a unit test written against the same blind spot wouldn't have caught - the fix came from actually running the feature, per the "test in a browser/CLI, not just the test suite" principle.

### 🔧 Design Decisions

- **Isolated CLI validation, not a repo-polluting one.** Every path `main_v2.py` touches (`config.json`, `data/tasks.json`, `data/agent_states.json`, `data/budgets.json`) is relative to the process's *working directory*, not the script's location - so `submit`/`process`/`report` were exercised from a throwaway temp directory (with `config.json` copied in) rather than against the real committed `data/tasks.json`, which already had genuine task history in it.
- **Record the same numbers, don't recompute them.** `analytics.record_task()` is called with the exact same `quality_score`/`duration`/`mock_cost`/`success` values already computed for `agent_state.record_performance()`, so the two metrics systems can't silently disagree about what happened in a given run.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files
- End-to-end CLI run in an isolated temp directory: `submit` x2 → `process` → `status` (shows real budget/capacity per department) → `report` (showed `Total Tasks: 0` before the fix, `Total Tasks: 2` after)
- `test_analytics_wiring.py`: all 3 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (all 8 test files including today's five checkpoints): all pass

### 📝 Next Steps

- `avg_turnaround_time` in the report is currently near-zero because mock-mode LLM calls are instant wall-clock time, not the task's real `estimated_hours` - worth deciding which of the two "duration" concepts (wall-clock vs. business estimate) `performance.py` should actually track
- A `resume_step()`/retry path for `BLOCKED` workflow steps once budget frees up
- Give the `bug_fix` workflow's "Verification" step a real cost too
- Apply the injectable-dependency pattern (used for `PerformanceAnalytics.generate_report()` today) to `DepartmentHeadAgent` so its own tests can stop touching the shared global registries

### 📂 Files Modified

- `main_v2.py` (`status` shows budget/capacity, new `report` command)
- `task_executor_v2.py` (`run()` now calls `analytics.record_task()`)
- `test_analytics_wiring.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 SIXTH CHECKPOINT TODAY: DURATION METRICS WERE MEASURING THE WRONG THING

**Summary:** The previous checkpoint's own "Next Steps" flagged this immediately: `run()` recorded `time.time() - start` (real wall-clock time of a mock LLM call - milliseconds) as a task's "duration" in both `agent_state` and `performance.analytics`, regardless of whether the task represented 1 hour or 40. That makes `avg_turnaround_time`, `get_top_performers("speed")`, and the `workload_rebalance`/`skill_gap` recommendations in `performance.py` permanently dead code - none of them could ever see anything but near-zero.

### ✅ What Changed

**`task_executor_v2.py`**
- `DepartmentHeadAgent.run()` now records `effort_hours = estimated_hours` (the same business-hours estimate the budget check upstream already priced the task at) as the task's duration in both `agent_state.record_performance()` and `analytics.record_task()`, instead of the wall-clock `time.time() - start` of the mock LLM call
- The real wall-clock `duration` is kept exactly as before for the `agent_complete` trace event and `TaskExecutor.execute()`'s own timing - this is about what gets recorded as *business* effort in the analytics layer, not about removing wall-clock tracing

**`test_effort_metrics.py` (new)** — two scenarios, both passing and idempotent:
1. A task estimated at 6 hours records `avg_duration_hours == 6.0`, not a fraction of a millisecond - while the `agent_complete` event still separately shows the real (sub-second) wall-clock time of the mock call
2. Three consecutive 12-hour tasks push `avg_duration_hours` over the `workload_rebalance` threshold (`> 8`) and the recommendation actually fires - previously impossible since `avg_duration_hours` could never exceed a fraction of a second in mock mode

### 🔧 Design Decisions

- **One duration concept per purpose, not one number doing two jobs.** Wall-clock time answers "how long did the system take to process this" (ops/tracing question); business-hours effort answers "how much work did this represent" (capacity/workload question). Conflating them meant the wall-clock answer wasn't wrong for tracing, but silently broke every business-facing metric derived from it. This mirrors the cost model already in place: cost = `estimated_hours * cost_per_hour`, so duration = `estimated_hours` keeps the two consistent.
- **Idempotent by construction, again.** Both new test scenarios use a task whose `estimated_hours` never varies across repeated runs against the department name, so the running average stays exactly `6.0`/`12.0` no matter how many times the test has run before - no need for delta-based assertions this time.

### ✅ Validation

- `python -m py_compile` clean
- `test_effort_metrics.py`: both scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (all 9 test files now, including today's six checkpoints): all pass

### 📝 Next Steps

- A `resume_step()`/retry path for `BLOCKED` workflow steps once budget frees up
- Give the `bug_fix` workflow's "Verification" step a real cost too
- Apply the injectable-dependency pattern to `DepartmentHeadAgent` so its own tests can stop touching the shared global registries
- `TaskExecutor.execute_parallel()` doesn't pass `estimated_hours` through at all (always defaults to `1.0`) - CLI-submitted tasks never get a real business-hours estimate unless a future change routes one in from task metadata

### 📂 Files Modified

- `task_executor_v2.py` (duration metrics now record business effort hours, not wall-clock time)
- `test_effort_metrics.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 SEVENTH CHECKPOINT TODAY: ESTIMATED HOURS NEVER LEFT THE CLI

**Summary:** The previous checkpoint fixed how `DepartmentHeadAgent.run()` records `estimated_hours` once it has one - but nothing ever gave it one. `TaskExecutor.execute()`/`execute_parallel()` never accepted or passed through an `estimated_hours` argument, so every task processed by `main_v2.py process` silently used `run()`'s default of `1.0h`, and `submit_task()` had no way to specify anything else. Every task ever submitted through the actual CLI was billed and measured identically regardless of real size. Closed the whole path end to end.

### ✅ What Changed

**`task_executor_v2.py`**
- `TaskExecutor.execute(department, task_description, estimated_hours=1.0)`: now passes `estimated_hours` through to `agent.run()` instead of dropping it
- `TaskExecutor.execute_parallel()`: now accepts `(department, description)` *or* `(department, description, estimated_hours)` tuples - a bare 2-tuple still defaults to `1.0h`, so this is backward compatible

**`main_v2.py`**
- `submit_task()` takes `estimated_hours` and stores it on the task record; the `submit` command gained a `--hours N` flag (defaults to `1.0`)
- `process_tasks()` reads each task's stored `estimated_hours` (via `.get(..., 1.0)`, so tasks submitted before this change still work) and passes it through on both the parallel and sequential paths

**`test_executor_hours_threading.py` (new)** — three scenarios, all passing and idempotent:
1. `execute()` with `estimated_hours=5.0` bills exactly `5h * $100/hr = $500`
2. `execute_parallel()` bills each task in a batch for its *own* hours (`2h` and `3h` tasks together cost `$500`, not `2 * default`)
3. A bare `(department, description)` 2-tuple still defaults to `1.0h` - confirms the extension didn't break the original tuple shape

### 🔧 How This Was Found

Same as the previous checkpoint - running the real CLI pipeline (`submit --hours 0.5`, `submit --hours 30`, `process --sequential`, `report`) in an isolated temp directory rather than trusting unit tests alone. Before this fix, both tasks would have cost identically ($100 flat, `1.0h` each) and the report's `avg_turnaround_time` still would have been meaningless despite the previous checkpoint's fix, because nothing was actually feeding `run()` a real number. After: `$3,050` total spend (`$50` + `$3,000`, matching a `0.5h` and a `30h` task exactly), `15.2h` avg turnaround, and the `workload_rebalance` recommendation fired for real through the full CLI path - three of today's checkpoints visibly compounding correctly together.

### ✅ Validation

- `python -m py_compile` clean
- End-to-end CLI run (temp dir, two tasks at `0.5h` and `30h`) confirmed exact expected spend and a real, non-zero average turnaround
- `test_executor_hours_threading.py`: all 3 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (10 test files now): all pass

### 📝 Next Steps

- A `resume_step()`/retry path for `BLOCKED` workflow steps once budget frees up
- Give the `bug_fix` workflow's "Verification" step a real cost too
- Apply the injectable-dependency pattern to `DepartmentHeadAgent` so its own tests can stop touching the shared global registries
- `DepartmentManager.route_task()` auto-detects department from keywords but there's still no auto-estimation of `estimated_hours` from task text - it's purely a manual `--hours` flag today

### 📂 Files Modified

- `task_executor_v2.py` (`execute()`/`execute_parallel()` thread `estimated_hours` through)
- `main_v2.py` (`submit --hours`, stored on the task, passed through `process`)
- `test_executor_hours_threading.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 EIGHTH CHECKPOINT TODAY: WORKFLOW RETRY, BUG-FIX COST PARITY, AND A REAL BUG CAUGHT BY THE NEW TEST

**Summary:** Two remaining items from the workflow-budget checkpoint: the `bug_fix` template's "Verification" step had no cost (an asymmetry with `feature_request`'s costed steps), and a `BLOCKED` workflow step had no way to resume - it stayed stuck forever even after the department's budget was topped up. Fixing the second one surfaced a real bug in the first workflow-budget checkpoint's code.

### ✅ What Changed

**`workflows.py`**
- `create_bug_fix_workflow()`'s "Verification" step now costs `$500` against `engineering`'s budget (lighter than the feature workflow's `$3,000`/`$15,000` steps, matching a fast-track bug fix)
- New `WorkflowEngine.retry_blocked_step(instance_id)`: re-attempts a `BLOCKED` step's budget reservation (e.g. after a top-up or reallocation). On success, flips the step back to `IN_PROGRESS` and the workflow back to `IN_PROGRESS`; on failure, updates the error message and stays blocked - it doesn't raise or silently no-op
- **Bug fix**: `get_next_step()`'s blocked branch never set `instance.current_step` before returning `None` - so a blocked instance had no record of *which* step was blocked, and `retry_blocked_step()` (which reads `instance.current_step`) could never find it. Now sets it in both the success and blocked paths.

**`test_workflow_retry.py` (new)** — three scenarios, all passing and idempotent:
1. A step blocked for insufficient funds resumes correctly once the department's budget is topped up, and can then be approved normally
2. Retrying again with still-insufficient funds fails cleanly (correct error message, stays blocked) rather than crashing
3. Retrying a workflow with nothing blocked is a clean no-op ("No blocked step to retry"), not a silent success

### 🔧 How This Was Found

Writing the retry test the straightforward way - block a step, top up the budget, call `retry_blocked_step()`, expect it to succeed - failed immediately with "No blocked step to retry" even though the step was visibly `BLOCKED` in `instance.step_status`. That's what led to checking `get_next_step()`'s blocked branch and finding `instance.current_step` was never assigned there, only in the success path a few lines below it.

### 🔧 Design Decisions

- **Retry doesn't retry automatically.** `retry_blocked_step()` is deliberately a manual call, not something the engine polls for on its own - there's no background loop in this system, and auto-retrying on every unrelated event would be surprising. A caller (CLI command, scheduled check, etc.) decides when it's worth trying again.
- **Bug caught by writing the test the way a real caller would use the feature**, not by reading the implementation and confirming it matched itself - the same "run the actual pipeline" lesson from the two checkpoints before this one, just applied to a unit test instead of the CLI.

### ✅ Validation

- `python -m py_compile` clean
- `test_workflow_retry.py`: all 3 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (11 test files now, spanning all eight of today's checkpoints): all pass
- Re-ran `test_workflows.py` specifically to confirm the bug_fix workflow's new `$500` verification cost doesn't change its existing (unmodified) test's behavior

### 📝 Next Steps

- Apply the injectable-dependency pattern to `DepartmentHeadAgent` so its own tests can stop touching the shared global registries
- `DepartmentManager.route_task()` still has no auto-estimation of `estimated_hours` from task text - it's a manual `--hours` flag only
- `retry_blocked_step()` has no CLI surface yet (`main_v2.py` has no workflow commands at all - workflows are still test/script-only, unlike task execution)

### 📂 Files Modified

- `workflows.py` (`retry_blocked_step()`, bug_fix "Verification" cost, `current_step` bug fix)
- `test_workflow_retry.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 NINTH CHECKPOINT TODAY: DEPENDENCY INJECTION FOR DepartmentHeadAgent

**Summary:** Every checkpoint since the second one flagged the same unresolved item: `DepartmentHeadAgent` (and the `AgentDecisionEngine`/`OrganizationDecisionMaker` it drives) hardcoded the shared global `agent_state_registry`, `budget_manager`, `capacity_manager`, and `analytics` singletons. Every test exercising `run()` had to touch real shared files (`data/agent_states.json`, `data/budgets.json`, `data/performance_metrics.json`) and manually clean up afterward (`git checkout --`, stray `rm -f`s) - eleven test files' worth of that ritual today alone. Closed it.

### ✅ What Changed

**`agent_decisions.py`**
- `AgentDecisionEngine.__init__(agent_state, registry=None)`: defaults to the shared global agent registry, but `find_best_delegate()` now reads from `self.registry` instead of the bare module-level name
- `OrganizationDecisionMaker.__init__(leader_agent_id, registry=None)`: same pattern

**`task_executor_v2.py`**
- `DepartmentHeadAgent.__init__` gains `agent_state_registry`, `budget_manager`, `capacity_manager`, `analytics` params - each `None` by default (falls back to the shared singleton, so every existing call site's behavior is unchanged), stored as `self.<name>` and used everywhere in `decide_on_task()`/`run()` instead of the bare module names
- `TaskExecutor.__init__` gains the same four params and passes them through to every `DepartmentHeadAgent` it creates in `get_agent_for_department()` - so injecting once at the executor level covers every agent it spins up on demand, not just one constructed by hand

**`test_department_head_isolation.py` (new)** — two scenarios, both passing and idempotent:
1. A fully-injected `DepartmentHeadAgent` runs a task and hashes/mtimes of all three shared data files are proven byte-for-byte unchanged before vs. after - while the *injected* isolated instances correctly recorded the spend, agent registration, and metrics
2. A `TaskExecutor` constructed with injected dependencies propagates them to an agent it creates on demand via `execute()`, not just one built by hand

### 🔧 Design Decisions

- **`None` means "use the global," never "use nothing."** Every new parameter defaults to `None` and falls back to the pre-existing module-level singleton - so this is purely additive. No existing call site (including every one of today's earlier eight checkpoints' tests, and `main_v2.py`, which passes none of these) needed to change to keep working exactly as before. Verified by re-running the full pre-existing 11-file suite unmodified.
- **Left the eight existing tests as they are.** Rewriting `test_resource_gating.py` etc. to use full injection was tempting but riskier than it's worth right now - they're stable, passing, and their "unique `qa_*` name + manual cleanup" pattern still works. The new capability is proven with a dedicated test instead of a risky retrofit of working code. Migrating them is a legitimate future cleanup, not a requirement.
- **Verified the CLI path too**, not just tests: re-ran `main_v2.py submit`/`process`/`report` end to end in a temp directory to confirm the refactor didn't change default (non-injected) behavior.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files
- `test_department_head_isolation.py`: both scenarios pass, run twice back-to-back to confirm idempotency, and confirmed via `git status` that no shared or scratch files were left behind (its own `main()` cleans up its private data files)
- Full suite (12 test files, all nine of today's checkpoints): all pass, unmodified
- End-to-end CLI run in a temp directory (`submit --hours 2` → `process` → `report`) confirmed unchanged default behavior

### 📝 Next Steps

- Optionally migrate the eight pre-DI test files to use full injection now that the capability exists, retiring their manual shared-file cleanup
- `DepartmentManager.route_task()` still has no auto-estimation of `estimated_hours` from task text - manual `--hours` flag only
- Workflows (`workflows.py`) are still test/script-only from the CLI's perspective - `main_v2.py` has no workflow commands

### 📂 Files Modified

- `agent_decisions.py` (`AgentDecisionEngine`/`OrganizationDecisionMaker` accept an injectable registry)
- `task_executor_v2.py` (`DepartmentHeadAgent`/`TaskExecutor` accept injectable registry/budget/capacity/analytics)
- `test_department_head_isolation.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TENTH CHECKPOINT TODAY: WORKFLOWS HAD ZERO PERSISTENCE

**Summary:** Started scoping a CLI surface for workflows (a natural next step after today's CLI wiring for budgets/capacity/performance) and found something more fundamental blocking it entirely: `WorkflowEngine` kept `self.instances`/`self.templates` in plain in-memory dicts with no `save()`/`load()` at all - unlike every other stateful module today (`agent_state.py`, `budgets.py`, `performance.py`), which all persist to JSON. Since every CLI command is a fresh Python process, a CLI command to start a workflow and a later command to advance it would never see the same state - the second command's workflow_engine would be empty. Wiring CLI commands on top of that would have been building on nothing. Fixed the actual gap first.

### ✅ What Changed

**`workflows.py`**
- `WorkflowEngine.__init__(data_file="data/workflows.json")`: loads persisted instances on construction
- New `load()`/`save()`: (de)serializes `WorkflowInstance` objects, converting the `WorkflowStatus`/`StepStatus` enum fields to/from their `.value` (plain JSON can't represent Python enums directly)
- Every mutating method (`create_instance`, `start_instance`, `complete_step`, `approve_step`, `retry_blocked_step`, `get_next_step` - both its success and blocked paths - and `check_complete`) now calls `self.save()`
- **Templates are deliberately NOT persisted** - they're supplied by code (`create_feature_request_workflow()`, etc.) and must be re-registered with `register_template()` at the start of every process, the same way `DepartmentManager` re-reads `config.json` fresh each run instead of caching department definitions to disk. A restored instance whose template hasn't been re-registered yet still exists (its data survived), it just can't be advanced until the caller registers the matching template again.

**`test_workflow_budget.py` / `test_workflow_retry.py`**
- Both previously constructed a fresh `WorkflowEngine()` specifically to stay isolated from the global `workflow_engine` singleton. With persistence now defaulting to the *same* `data/workflows.json` path, those "fresh" engines would have silently shared a file with the global singleton and with each other across runs. Pointed both at an isolated `data/test_workflow_engine.json` instead - the same fix pattern used throughout today for every other shared singleton.

**`test_workflow_persistence.py` (new)** — two scenarios, both passing and idempotent:
1. A workflow instance created and partially advanced by one `WorkflowEngine` object is fully visible to a second, completely separate `WorkflowEngine` object pointed at the same file (simulating two CLI process invocations) - including being driven the rest of the way to completion by the second object
2. Templates are confirmed *not* to survive between engine objects (by design): a fresh engine with the instance's data but no `register_template()` call can see the instance but can't advance it

### 🔧 How This Was Found

Not by running code and seeing a wrong answer, this time - by reading `WorkflowEngine.__init__` while scoping the next feature and noticing it had no `data_file` parameter or `load()`/`save()` at all, unlike every sibling module. Worth calling out because it's the one checkpoint today found by inspection rather than by running the pipeline end-to-end - both approaches earned their keep today.

### 🔧 Design Decisions

- **Persist instances, not templates.** Templates are static code, safe to reconstruct identically every process start; instances are the actual mutable state a real workflow run needs to survive a restart. Persisting templates too would mean two competing sources of truth (the code and the file) for something that should only ever come from code.
- **Save on every mutation, not batched.** Mirrors the pattern already used in `agent_state.py`/`budgets.py`/`performance.py` - a crash between "mutate in memory" and "write to disk" is a real risk with batching; committing state's cheap enough here to do it every time instead.

### ✅ Validation

- `python -m py_compile` clean
- `test_workflow_persistence.py`: both scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (13 test files now): all pass, including the two updated tests confirmed to still isolate correctly from the global singleton
- Confirmed `main_v2.py` doesn't import `workflows.py` at all yet, so this change has no CLI-facing effect until that wiring happens

### 📝 Next Steps

- Add the actual CLI surface for workflows to `main_v2.py` (`workflow start/next/approve/retry/status`) - the reason this checkpoint happened, now unblocked
- Optionally migrate the eight pre-DI test files to use full injection now that the capability exists
- `DepartmentManager.route_task()` still has no auto-estimation of `estimated_hours` from task text

### 📂 Files Modified

- `workflows.py` (`WorkflowEngine` persistence: `load()`/`save()`, called from every mutating method)
- `test_workflow_budget.py` / `test_workflow_retry.py` (isolated `data_file` to avoid colliding with the global singleton)
- `test_workflow_persistence.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 ELEVENTH CHECKPOINT TODAY: WORKFLOWS ARE FINALLY REACHABLE FROM THE CLI

**Summary:** The previous checkpoint fixed the reason a workflow CLI would have been pointless (no persistence); this one builds the CLI surface itself. Before today, `workflows.py` was purely test/script-only - a working, tested engine nobody could actually drive without writing Python.

### ✅ What Changed

**`main_v2.py`**
- New `workflow` command group: `list`, `start <template_id> [key=value ...]`, `next <instance_id>`, `complete <instance_id> <step_id>`, `approve <instance_id> <step_id> [--reject]`, `retry <instance_id>`, `status <instance_id>`
- `init_workflows()` re-registers both known templates (`feature_request`, `bug_fix`) at the start of every workflow command - required because (per the previous checkpoint) templates are never persisted, only instances are
- `workflow_next()` distinguishes three outcomes instead of just "step or nothing": an executable next step (with its approval/cost info printed inline), a `BLOCKED`/`ESCALATED` instance (prints the reason and suggests `workflow retry`), or a genuinely complete/exhausted instance

### 🔧 How This Was Found And Validated

Same "run it for real" pattern as every CLI checkpoint today. Ran the entire lifecycle as separate `python3` process invocations (not a single Python session) in an isolated temp directory, proving persistence actually works end-to-end through the CLI, not just in `test_workflow_persistence.py`:
1. `start feature_request` → `next` (Intake) → `complete` → `next` (Design, `$3,000` from design's budget, needs approval) → `approve` → `status` shows `29%` progress, `design: approved` — then re-ran `status` as a *third* separate process and got the identical output
2. `start bug_fix` with a deliberately tiny `engineering` budget → `next`/`complete` through triage and fix → `next` on Verification correctly reports `⚠ Blocked: ... Insufficient budget` and suggests the retry command → `retry` (still broke) fails with the same reason → topped up the budget → `retry` succeeds → `status` shows the resumed step `in_progress`

### 🔧 Design Decisions

- **No dedicated CLI unit test file, matching the `status`/`report` commands from earlier today.** These functions are thin wrappers around already-unit-tested `WorkflowEngine` methods; the value is in exercising the real process-boundary behavior (persistence, template re-registration, argument parsing), which only an actual multi-process CLI run demonstrates - a unit test importing the wrapper functions directly would share the same Python process and miss exactly the bug class the previous checkpoint fixed.
- **`workflow_next()`'s three-way branch is deliberate.** Collapsing "blocked" and "genuinely nothing to do" into one message would hide the one piece of information a user needs most when a workflow stalls: whether there's an action (`retry`) or not.

### ✅ Validation

- `python -m py_compile` clean
- Full end-to-end CLI runs (multiple separate process invocations) for both `feature_request` (happy path with a real budget-gated approval) and `bug_fix` (block → retry-fails → top-up → retry-succeeds)
- Full suite (13 test files, unmodified): all pass

### 📝 Next Steps

- Optionally migrate the eight pre-DI test files to use full injection now that the capability exists
- `DepartmentManager.route_task()` still has no auto-estimation of `estimated_hours` from task text
- `main_v2.py`'s `submit`/`process` task pipeline and `workflows.py`'s workflow pipeline are still two entirely separate systems (a submitted task never becomes a workflow step and vice versa) - worth deciding whether/how they should connect

### 📂 Files Modified

- `main_v2.py` (new `workflow` command group: list/start/next/complete/approve/retry/status)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TWELFTH CHECKPOINT TODAY: TASKS NO LONGER DEFAULT TO A FLAT 1.0h

**Summary:** The last standing item from the "estimated_hours" thread that's run through several of today's checkpoints: `submit`ting a task without `--hours` always defaulted to exactly `1.0h`, so "fix a typo" and "full platform migration" were billed and measured identically unless a human remembered the flag. Added a small keyword-based heuristic default - always overridable, never a substitute for a real estimate.

### ✅ What Changed

**`departments.py`**
- New `DepartmentManager.estimate_hours(description)`: scans the description for `HIGH_EFFORT_KEYWORDS` (migration, redesign, overhaul, rewrite, rearchitect, platform → `16.0h`) or `LOW_EFFORT_KEYWORDS` (typo, quick, small, minor, tweak, trivial, copy change → `0.5h`), falling back to the unchanged `1.0h` default when neither matches. If a description matches both tiers, high-effort wins - understating a genuinely large task is the worse failure mode of the two.

**`main_v2.py`**
- `submit_task()`'s `estimated_hours` param now defaults to `None`, and resolves via `DepartmentManager.estimate_hours(description)` when the caller doesn't supply one - mirroring the existing `department=None` → `route_task()` pattern already used for department routing
- The `submit` CLI command's `--hours` parsing simplified accordingly: `None` when not given, letting `submit_task()` do the heuristic lookup, instead of computing it in the argument-parsing block

**`test_effort_estimation.py` (new)** — five scenarios, all passing and idempotent:
1. Low-effort keywords estimate `0.5h`
2. High-effort keywords estimate `16.0h`
3. A neutral description falls back to `1.0h`
4. A description matching both tiers resolves to the high-effort estimate
5. `submit_task()` uses the heuristic by default but an explicit `estimated_hours` still overrides it - run against a fully isolated `tempfile.TemporaryDirectory()`, not the shared `data/tasks.json`

### 🔧 Design Decisions

- **The default lives in `submit_task()`, not the CLI argument parser.** Resolving `None` → heuristic inside the function itself (like `department=None` → `route_task()`) means *any* caller of `submit_task()` gets the smart default, not just the one CLI code path that happened to compute it. A future second caller (a batch import script, say) gets this for free.
- **Explicit always beats heuristic, unconditionally.** `--hours 99` on a description containing "quick" and "typo" still resolves to `99.0`, verified directly - the heuristic is scoped strictly to "the caller didn't say," never treated as a correction to what they did say.
- **Coarse and keyword-only, deliberately.** This isn't meant to be a good estimator - it's meant to stop a flat, uniform default from making every duration/cost metric in the system (several of which today's earlier checkpoints just went to the trouble of fixing) trivially wrong for the common case of *not* passing `--hours`. A real system would get its estimate from a human or the `feature_request` workflow's own "Engineering Estimate" step.

### ✅ Validation

- `python -m py_compile` clean
- End-to-end CLI run in a temp directory: four submits (typo fix, platform migration, ordinary task, and an explicit `--hours 99` override) all produced the expected `estimated_hours` in `data/tasks.json`
- `test_effort_estimation.py`: all 5 scenarios pass, run twice back-to-back to confirm idempotency (fully isolated via `tempfile.TemporaryDirectory()`, so no shared-state cleanup needed for this one)
- Full suite (14 test files now): all pass

### 📝 Next Steps

- Optionally migrate the eight pre-DI test files to use full injection now that the capability exists
- `main_v2.py`'s task pipeline (`submit`/`process`) and `workflows.py`'s workflow pipeline are still two entirely separate systems
- The keyword lists are hardcoded constants in `departments.py`; if departments end up wanting different effort vocabularies, they'd need to move into `config.json` the way department keywords already did

### 📂 Files Modified

- `departments.py` (`estimate_hours()`, effort-tier keyword constants)
- `main_v2.py` (`submit_task()` defaults to the heuristic when `estimated_hours` isn't given)
- `test_effort_estimation.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 THIRTEENTH CHECKPOINT TODAY: PHASE 5 KICKOFF - STRATEGIC BUDGET REALLOCATION

**Summary:** Every checkpoint so far this session sat inside Phases 2-4 of the roadmap. With the "estimated_hours" thread closed, this is the first Phase 5 ("strategic planning") work: `budgets.py` has computed exactly which departments are underspending and which are approaching their limit since checkpoint 1 today (`over_budget_departments()`, `DepartmentBudget.utilization_pct()`) - nothing has ever acted on that signal. A department stuck at 95% utilization stayed there forever even while another sat at 5%.

### ✅ What Was Built

**`strategy.py` (new module)**
- `ReallocationProposal`: a single proposed transfer (`from_department`, `to_department`, `amount`, `reason`)
- `StrategicPlanner`:
  - `propose_reallocations(low_threshold=0.3, high_threshold=0.85)`: finds departments at or below `low_threshold` utilization (donors) and at or above `high_threshold` (recipients), then greedily matches the largest donors to the largest shortfalls. A donor never gives away more than `available() - reserve_pct * allocated` (default reserve `20%`) - "underspending so far" isn't "safe to zero out." A recipient's need is computed as exactly enough to bring it back down to `high_threshold`, not further.
  - `apply_reallocations(proposals)`: moves `allocated` between the named departments and persists
  - `rebalance()`: propose + apply in one call, for a caller (e.g. an automated periodic job) that trusts the heuristic outright rather than reviewing first
- Deliberately keys off **budget** utilization, not capacity utilization - there's no "hire more staff with money" mechanic in this system, so capacity isn't the right signal for a *budget* move even though `CapacityManager` computes a similar-looking number

**`main_v2.py`**
- New `strategy [--apply]` command: dry-run by default (prints proposals with their reasoning), `--apply` actually executes them

**`test_strategic_planning.py` (new)** — four scenarios, all passing and idempotent (self-cleaning, isolated `BudgetManager` instances throughout):
1. A department at 5% utilization and one at 95% produce exactly one proposal for the exact computed shortfall (`$1,176.47`, verified against the formula by hand)
2. A donor with a huge available balance next to a recipient with an enormous need still only gives up to its reserve-protected limit (`$750`, not more)
3. Two departments both sitting in the middle (50%/40%) produce zero proposals
4. `apply_reallocations()`/`rebalance()` actually mutates `allocated` on both departments and persists - confirmed by re-reading the file with a fresh `BudgetManager`

### 🔧 How This Was Found And Validated

Followed the roadmap directly this time rather than a bug-hunt: with Phases 2-4's loose ends from today closed, the daily task's own phase list named Phase 5 ("strategic planning, compensation, etc.") as the next unclaimed area, and `budgets.py`'s already-computed utilization signal was the obvious concrete thing to act on. Validated end-to-end via the real CLI in a temp directory: manually created a 96%-utilized `engineering` next to a 3%-utilized `design`, ran `strategy` (dry run, showed the exact proposal), then `strategy --apply` and confirmed via `status` that `engineering` dropped to exactly `85%` (the threshold, not below it) and the `[!] Over-budget: engineering` flag from checkpoint 3 disappeared.

### 🔧 Design Decisions

- **Reserve-protected donors, exact-shortfall recipients.** Giving away 100% of a donor's unspent budget just because it's underspending *today* would be short-sighted; the `reserve_pct` exists because "no work happened yet" isn't the same as "will never need this money." Recipients only get exactly enough to reach the threshold, not an arbitrary amount, so `apply()` doesn't overcorrect.
- **Dry-run by default in the CLI**, matching the caution used everywhere else this system asks for money to move (`reserve_funds`, `approve_decision`) - reallocating a department's budget is a real decision, not something that should happen silently on every `strategy` invocation without `--apply`.

### ✅ Validation

- `python -m py_compile` clean
- End-to-end CLI run in a temp directory: `strategy` (empty state → no-op), manual imbalance setup, `strategy` (dry run, correct proposal), `strategy --apply`, `status` confirming the exact resulting percentages and the over-budget flag clearing
- `test_strategic_planning.py`: all 4 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (15 test files now): all pass

### 📝 Next Steps

- Phase 5's other named area, "compensation": today's `cost_per_hour` is a flat `$100` default for every `DepartmentHeadAgent` regardless of the agent's own `skill_level`/`agent_type` - a real compensation model would derive it from the agent's profile
- Optionally migrate the eight pre-DI test files to use full injection now that the capability exists
- `main_v2.py`'s task pipeline and `workflows.py`'s workflow pipeline are still two entirely separate systems

### 📂 Files Modified

- `strategy.py` (new)
- `main_v2.py` (new `strategy [--apply]` command)
- `test_strategic_planning.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 FOURTEENTH CHECKPOINT TODAY: PHASE 5, PART 2 - AGENT COMPENSATION

**Summary:** The previous checkpoint's "Next Steps" named Phase 5's other listed area directly: "compensation" - every `DepartmentHeadAgent` billed at a flat `$100/hr` regardless of whether it represented a CEO or a junior coordinator. Derived compensation from the agent's own profile instead.

### ✅ What Changed

**`agent_state.py`**
- New `AgentProfile.hourly_rate()`: `BASE_HOURLY_RATE_BY_TYPE` (`LeaderAgent` $250, `SpecialistAgent` $180, `ManagerAgent` $150, `CoordinatorAgent` $120, unmapped types fall back to the old flat `$100`) times a `skill_level` multiplier (`0.6 + 0.2 * skill_level`: skill 1 → `0.8x`, skill 5 → `1.6x`)

**`task_executor_v2.py`**
- `DepartmentHeadAgent.__init__`'s `cost_per_hour` now defaults to `None` and, when not explicitly given, resolves to `self.agent_state.profile.hourly_rate()` - reordered the constructor so `agent_state` is resolved *before* the rate is computed, since the rate needs the profile

**`test_compensation.py` (new)** — five scenarios, all passing and idempotent:
1. Higher-seniority `agent_type`s earn more at the same skill level (`CoordinatorAgent < ManagerAgent < SpecialistAgent < LeaderAgent`)
2. Higher `skill_level` earns more within the same type
3. An unrecognized `agent_type` falls back cleanly to the flat rate, no crash
4. A `DepartmentHeadAgent` with no explicit `cost_per_hour` bills at its own profile's derived rate end to end (spend matches `hours * hourly_rate()` exactly)
5. An explicit `cost_per_hour` still overrides the derived rate unconditionally

### 🔧 Two Existing Tests Broke, As Expected, And Were Fixed Correctly

`test_executor_hours_threading.py` and `test_department_head_isolation.py` both construct agents through `TaskExecutor` without an explicit `cost_per_hour`, so they'd been implicitly relying on the old flat `$100` default. Both failed immediately after this change - exactly as anticipated before making it, since every *other* test explicitly passes `cost_per_hour=100`/`=50`. Fixed by computing the expected fallback rate from `AgentProfile(...).hourly_rate()` directly in each test (`$180/hr` for the `ManagerAgent`/skill-3 fallback profile) instead of hardcoding a number - so these tests stay correct if the rate table in `agent_state.py` ever changes, rather than needing a manual update again.

### 🔧 Design Decisions

- **The rate table is a deliberately simple, editable set of module-level constants**, not a config-driven system - compensation policy is exactly the kind of thing that's fine to hardcode once and adjust by editing four numbers, unlike per-department budgets (which do need to vary per deployment and so live in `config.json`).
- **Verified the two breakages by running the full suite immediately after the change**, not by reasoning about it in the abstract - confirmed the prediction (which tests would break) matched what actually broke before touching either file.

### ✅ Validation

- `python -m py_compile` clean
- `test_compensation.py`: all 5 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (16 test files now): all pass, including the two fixed pre-existing tests
- End-to-end CLI run in a temp directory (`submit --hours 2` for both `engineering` and `design`, `process`, `status`) confirmed both departments now show `$360` spent (`2h * $180/hr` fallback rate) instead of the old flat `$200`

### 📝 Next Steps

- **Found while validating, out of scope for this checkpoint:** `config.json`'s richer per-department agent definitions (e.g. `engineering_head`: `skill_level: 4`) are never actually loaded into the registry - `DepartmentHeadAgent`'s fallback profile hardcodes `skill_level=3`/`ManagerAgent` regardless of what `config.json` says for that specific department head. Compensation now correctly derives from *whatever* profile an agent has, but nothing currently seeds the *richer* profile `config.json` already describes.
- Optionally migrate the eight pre-DI test files to use full injection now that the capability exists
- `main_v2.py`'s task pipeline and `workflows.py`'s workflow pipeline are still two entirely separate systems

### 📂 Files Modified

- `agent_state.py` (`AgentProfile.hourly_rate()`, rate table constants)
- `task_executor_v2.py` (`DepartmentHeadAgent` derives `cost_per_hour` from the profile by default)
- `test_executor_hours_threading.py` / `test_department_head_isolation.py` (updated to compute the expected fallback rate instead of hardcoding `$100`)
- `test_compensation.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 FIFTEENTH CHECKPOINT TODAY: config.json'S AGENT PROFILES WERE NEVER LOADED

**Summary:** Flagged as a discovered-but-out-of-scope gap at the end of the previous checkpoint: validating compensation showed `engineering_head` and `design_head` both getting the exact same generic `skill_level=3` fallback profile, despite `config.json` defining `skill_level: 4` for both (and different `max_concurrent_tasks`, capabilities, and constraints per department). Nothing had ever read `config.json`'s `"agents"` section into the registry - every department head was, for compensation and decision-making purposes, identical regardless of what `config.json` said about it.

### ✅ What Changed

**`departments.py`**
- New `DepartmentManager.get_agent_config(agent_id)`: looks up `config.json`'s `"agents"` section by exact id (`"engineering_head"`, etc. - the same id `DepartmentHeadAgent` already constructs as `f"{department}_head"`), returns `None` if this specific agent isn't named there

**`task_executor_v2.py`**
- `DepartmentHeadAgent.__init__`'s fallback-profile branch now checks `get_agent_config()` first: if `config.json` defines this agent, builds its `AgentProfile` from that (real `skill_level`, `agent_type`, `expertise_areas`, `capabilities`, `constraints`, `max_concurrent_tasks`); only falls through to the generic stub (`skill_level=3`, `ManagerAgent`, minimal capabilities) for a department `config.json` doesn't name

**`test_config_agent_profiles.py` (new)** — three scenarios, all passing and idempotent:
1. `engineering_head` loads `config.json`'s real `skill_level` (`4`, not the generic fallback's `3`), `max_concurrent_tasks` (`4`), and capabilities (`code_review`, etc.) - and its derived `cost_per_hour` reflects that real skill level (`$210/hr`, not the `$180/hr` a skill-3 profile would produce)
2. `engineering` and `design` get *different* profiles from each other (`max_concurrent_tasks` 4 vs. 3) - this was the actual bug: every department head collapsed into one identical generic profile
3. A department `config.json` doesn't name still falls back to exactly the old generic stub, unchanged - purely additive

### 🔧 Why This Didn't Break Anything Already Passing

Checked before writing a single line: every one of the fifteen existing test files that construct a `DepartmentHeadAgent`/`TaskExecutor.execute()` directly uses a fictional `qa_*`-prefixed department name, never a real one (`engineering`, `design`, etc.) - `config.json` has no entry for any of those, so `get_agent_config()` returns `None` for all of them and every existing test's fallback-stub-based expectations are completely undisturbed. Confirmed by running the full suite immediately after the change, before writing the new test.

### ✅ Validation

- `python -m py_compile` clean
- End-to-end CLI run in a temp directory: `submit --hours 2` for both `engineering` and `design`, `process`, `status` showed `$420` spent for each (`2h * $210/hr`, up from the pre-fix `$360` at the generic skill-3 rate) and `data/agent_states.json` confirmed both registered at `skill_level: 4`
- `test_config_agent_profiles.py`: all 3 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (17 test files now): all pass unmodified, confirming zero impact on every pre-existing test

### 📝 Next Steps

- Only the five built-in department heads have `config.json` entries; `ceo`, `tech_lead`, and `product_coordinator` are defined there too but nothing in `task_executor_v2.py` ever constructs a `DepartmentHeadAgent` for those roles specifically (there's no leader/specialist/coordinator execution path, just department heads)
- Optionally migrate the earlier pre-DI test files to use full injection now that the capability exists
- `main_v2.py`'s task pipeline and `workflows.py`'s workflow pipeline are still two entirely separate systems

### 📂 Files Modified

- `departments.py` (`get_agent_config()`)
- `task_executor_v2.py` (`DepartmentHeadAgent` prefers `config.json`'s own profile over the generic stub)
- `test_config_agent_profiles.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 SIXTEENTH CHECKPOINT TODAY: WORKFLOW STEPS ACTUALLY GET DONE NOW

**Summary:** The biggest remaining gap standing between "a tested state machine" and "an autonomous business simulation": `workflow complete <instance_id> <step_id>` marked a step done by recording a hand-typed placeholder (`{"completed_via": "cli"}`) - no department, no agent, no LLM call, no budget draw, ever actually did the work. `main_v2.py`'s task pipeline (`submit`/`process`) and `workflows.py`'s workflow pipeline had been two entirely separate systems all day, flagged as a next step in four earlier checkpoints. Bridged them.

### ✅ What Changed

**`workflows.py`**
- New `WorkflowEngine.execute_step(instance_id, step_id, executor)`: looks up the step's real department and description, calls `executor.execute(department, description)`, and records the *actual* returned result via the existing `complete_step()` - instead of a caller supplying a placeholder
- `executor` is duck-typed (any object with `execute(department, description) -> dict`) rather than importing `task_executor_v2.TaskExecutor` directly - keeps `workflows.py` from taking on a hard dependency on the execution layer just to describe workflow shape, while still working directly with the real `TaskExecutor`

**`main_v2.py`**
- `workflow_complete()` now constructs a `TaskExecutor` and calls `workflow_engine.execute_step()` instead of hand-building a placeholder result - a step's completion now runs through the *exact same* budget/capacity/decision-engine gates every other task in the system goes through

**`test_workflow_execution.py` (new)** — three scenarios, all passing and idempotent, using a `FakeExecutor` (no real LLM/budget dependency, so the bridge mechanics are tested in isolation from the real execution layer):
1. `execute_step()` calls the executor with the step's real `owner_department` and a description built from the step's name/description, not a placeholder
2. The step's stored result is the executor's actual return value
3. An unknown instance or step id fails cleanly (`False`, no crash) and never calls the executor at all

### 🔧 How This Was Validated (And What It Revealed)

Ran the real bridge - `TaskExecutor`, real `LLMProvider`, real budgets - end to end via the CLI in a temp directory, not just the fake-executor unit test:
- `bug_fix`'s "Bug Triage" step, owned by `support`: `workflow complete` actually spun up `support_head` (loading its real `config.json` profile from checkpoint 15), ran the mock LLM, and drew exactly `$180` (`1h * $180/hr`, `support_head`'s real derived rate) from `support`'s budget - visible immediately in `status`
- `feature_request`'s "Design Specification" step (`requires_approval`, `$3,000` reservation from checkpoint 2): executing it drew a *separate* `$210` (`design_head`'s own per-task labor cost) on top of the still-held `$3,000` reservation, and approving afterward correctly settled to `$3,210` total (`$210` execution + `$3,000` confirmed reservation) - the two budget mechanisms (a workflow step's own line-item cost vs. the department agent's per-task labor cost) compose additively without double-charging or conflict, which is the economically sensible outcome, not a bug to fix

### 🔧 Design Decisions

- **Duck-typed executor, not an import.** `WorkflowEngine.execute_step()` never imports `TaskExecutor` - it just calls `.execute(department, description)` on whatever it's given. This is exactly the pattern `budgets.py`/`performance.py` already use for injectable dependencies, applied here to avoid a real architectural coupling (workflows describing shape vs. task_executor doing work) rather than just for test isolation.
- **`execute_step()` performs work, `approve_step()` still grants approval - deliberately not merged.** A step requiring approval still needs both calls in sequence. Collapsing them would mean "the work got done" and "a human/role signed off on it" become the same action, which defeats the purpose of `requires_approval` existing at all.

### ✅ Validation

- `python -m py_compile` clean
- `test_workflow_execution.py`: all 3 scenarios pass (fake executor, no shared state)
- Full end-to-end CLI validation of the *real* bridge: `bug_fix`'s triage step (real budget draw, confirmed via `status`) and `feature_request`'s approval-gated design step (confirmed the two budget mechanisms compose correctly)
- Full suite (18 test files now): all pass

### 📝 Next Steps

- `workflow_next()` still only *shows* the next step - a caller has to separately call `workflow complete` to actually run it. Could auto-execute non-approval steps on `next` directly, closing the loop further
- Only the five built-in department heads have `config.json` profiles; there's still no execution path at all for `ceo`/`tech_lead`/`product_coordinator` roles
- Optionally migrate the earlier pre-DI test files to use full injection now that the capability exists

### 📂 Files Modified

- `workflows.py` (`WorkflowEngine.execute_step()`)
- `main_v2.py` (`workflow_complete()` uses the real executor bridge)
- `test_workflow_execution.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 SEVENTEENTH CHECKPOINT TODAY: CLOSING A TESTING DEBT, NOT ADDING A FEATURE

**Summary:** Sixteen checkpoints today layered feature after feature onto `main_v2.py`'s core task pipeline (`submit_task`, `process_tasks`, `list_tasks`, `show_task`, `show_status`), and every single one of them was validated by a manual end-to-end CLI run in a temp directory - genuinely useful for catching integration bugs (it caught two real ones earlier today), but none of it left behind an automated regression test. This was a deliberate pause to pay down that debt rather than reach for another feature.

### ✅ What Was Built

**`test_main_cli_core.py` (new)** — seven scenarios, all passing and idempotent, covering the five core functions directly (not through a subprocess):
1. `submit_task()` with no `--dept`/`--hours` correctly routes the department (keyword match) and estimates hours (heuristic default)
2. `process_tasks(parallel=True)` actually executes queued tasks and marks them `completed` with a real result
3. `process_tasks(parallel=False)` (the sequential path) does the same
4. `process_tasks()` with nothing queued is a clean no-op (`"No queued tasks."`, no crash)
5. `list_tasks(status=...)` correctly filters - all/queued/completed views each show exactly the right rows
6. `show_task()` displays real task details and reports a missing task cleanly rather than crashing
7. `show_status()` reflects real, accumulated task/budget/capacity counts after a mixed sequence of submits and a process run

### 🔧 Design Decisions

- **Call the functions directly, not through a subprocess.** Every earlier CLI validation today shelled out to a fresh `python3 main_v2.py ...` process specifically to test process-boundary behavior (persistence, in particular). This test has no such requirement - it's testing the functions' own logic - so it imports `main_v2` and calls `submit_task()`/`process_tasks()`/etc. directly, using `contextlib.redirect_stdout` to capture and assert on their printed output. Much faster, and there's no reason to pay the subprocess cost when there's nothing about process boundaries to prove.
- **A custom `IsolatedCwd` context manager, not a bare `tempfile`/`os.chdir` pair repeated seven times.** Copies `config.json` into a fresh temp directory, `os.chdir`s into it, and restores the original directory and cleans up afterward - the same isolation approach `test_effort_estimation.py`'s fifth scenario used earlier today, extracted into something reusable across all seven scenarios here.
- **Verified the isolation claim, not just assumed it.** Ran `test_main_cli_core.py` alone (not the full suite) and diffed `git status` before/after - confirmed it leaves the tracked `data/agent_states.json` and every scratch file untouched. The pollution visible when running the full 19-file suite together comes entirely from the other eighteen (pre-existing) files, which still rely on the shared global singletons and their own manual cleanup - a fact worth recording plainly rather than letting it look like this new file caused it.

### ✅ Validation

- `python -m py_compile` clean
- `test_main_cli_core.py`: all 7 scenarios pass, run twice back-to-back to confirm idempotency
- Full suite (19 test files now): all pass
- Confirmed via `git status` diffing that this specific file, run alone, touches zero shared or scratch files

### 📝 Next Steps

- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones - considered today, deliberately left as-is: it would collapse the current `next`/`complete` separation, which mirrors `submit`/`process`'s intentional queued-vs-processed distinction, for a marginal convenience gain
- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically, only the five department heads
- Optionally migrate the earlier pre-DI test files to use full injection now that the capability exists

### 📂 Files Modified

- `test_main_cli_core.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 EIGHTEENTH CHECKPOINT TODAY: RETIRING THE MANUAL CLEANUP RITUAL

**Summary:** "Optionally migrate the earlier pre-DI test files to use full injection" has been on the Next Steps list since checkpoint 9 and deferred every time since as lower priority than whatever feature was next. Reframed it while thinking about whether `data/budgets.json`/`data/workflows.json`/`data/performance_metrics.json` should finally be added to `.gitignore` (to stop needing the `git checkout --`/`rm -f` ritual repeated after all seventeen prior checkpoints): gitignoring them would be the wrong fix. In a real deployment those files hold genuine state - budget spend history, workflow instances - exactly as real as `data/agent_states.json`/`data/tasks.json`, which *are* tracked. The actual problem was that four of today's test files still exercised the shared global singletons directly instead of injected instances, which is what caused the pollution requiring cleanup in the first place. Fixed the actual problem instead of hiding it.

### ✅ What Changed

**`test_resource_gating.py`, `test_analytics_wiring.py`, `test_effort_metrics.py`, `test_executor_hours_threading.py`**
- Each now constructs its own isolated `AgentRegistry`/`BudgetManager`/`CapacityManager`/`PerformanceAnalytics` (module-level, shared across that file's own test functions) and threads them into every `DepartmentHeadAgent`/`TaskExecutor` construction via a small `_agent()`/`_executor()` helper, instead of relying on the shared global defaults
- Each cleans up its own scratch files at the end of `main()`
- Assertions and printed output are byte-for-byte unchanged - confirmed by diffing behavior before/after the migration on every one of the twenty scenarios across these four files

**`test_budgets.py`, `test_performance_resources.py`, `test_workflow_budget.py`, `test_workflow_retry.py`**
- These already used isolated `data_file` paths (from earlier checkpoints) but never cleaned them up afterward. Added the missing `pathlib.Path(...).unlink(missing_ok=True)` cleanup to each.

### 🔧 What's Left, And Why It's Fine To Leave

`test_agent_decisions.py` and `test_performance.py` (both from Day 1/Day 2, before today) still use the shared global registry/analytics singletons *by original design* - they're explicitly testing persistence and cross-invocation state, which is the whole point of those singletons existing. Migrating them to isolated instances would defeat their own purpose, not fix a gap. `test_workflows.py` (also pre-existing) drives the global `workflow_engine` and, through it, the global `budget_manager` for the feature-request template's costed steps - same reasoning. These three are the only remaining sources of the (much smaller now) cleanup ritual, and they're not bugs.

### ✅ Validation

- `python -m py_compile` clean on all eight modified files
- Full suite (19 test files) run twice back-to-back: all pass both times, identical output
- `git status` diffed after each run: pollution reduced from ~12 stray/modified files down to exactly the 3 expected ones (`data/agent_states.json` modified, `data/budgets.json`/`data/workflows.json` created) - all traceable to the three pre-existing files named above, none to anything touched today

### 📝 Next Steps

- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones (deliberately left as-is, per checkpoint 17's reasoning)
- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically
- `README.md` and the other top-level docs (`SUMMARY.md`, `SESSION_SUMMARY.md`, etc.) haven't been touched since before today's eighteen checkpoints - worth a pass to reflect what actually exists now

### 📂 Files Modified

- `test_resource_gating.py` / `test_analytics_wiring.py` / `test_effort_metrics.py` / `test_executor_hours_threading.py` (migrated to full dependency injection)
- `test_budgets.py` / `test_performance_resources.py` / `test_workflow_budget.py` / `test_workflow_retry.py` (added missing scratch-file cleanup)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 NINETEENTH CHECKPOINT TODAY: THE README DOCUMENTED A SYSTEM THAT NO LONGER EXISTS

**Summary:** The previous checkpoint's Next Steps flagged it directly: `README.md` hadn't been touched since before today. Checking just how stale it was turned up something worse than "outdated" - it documented `python main.py submit ...` and referenced `task_executor.py`, and **neither file exists in this repo.** `main.py`/`task_executor.py` were the pre-`_v2` originals; the repo only has `main_v2.py`/`task_executor_v2.py` now. Anyone following the README's own Quick Start would hit "file not found" on the very first command.

### ✅ What Changed

**`README.md`** — full rewrite, `main_v2.py` as the only entry point:
- Features list rewritten to describe what the system actually does today (compensation, real budgets, capacity, persisted budget-gated workflows, strategic reallocation, combined analytics) instead of the original v1 feature set
- A CLI reference table covering all thirteen commands/subcommands, including every `workflow` subcommand and `strategy`, none of which existed in the old doc
- A worked example session, run for real end-to-end (not written from memory) to get exact output
- `config.json`'s `departments`/`agents` sections documented with real examples
- An architecture table mapping every module (including today's three new ones - `budgets.py`, `workflows.py`'s current form, `strategy.py`) to its responsibility
- A "how a task actually runs" walkthrough of the five-step `DepartmentHeadAgent.run()` pipeline built across today's checkpoints
- A Testing section explaining the DI-vs-shared-singleton split from the previous checkpoint
- Extending/Troubleshooting sections rewritten against the current commands

### 🔧 Two Inaccuracies Caught By Verifying Rather Than Writing From Memory

Drafted the example session's numbers first, then ran the actual commands to check them - caught two mistakes before they shipped:
1. Guessed engineering's spend at `$3,570`; the real run showed `$3,465` - a 16.0h task doesn't trigger approval-required billing (the threshold is strictly `estimated_hours > 16`, so exactly `16.0` doesn't cross it), so both tasks bill through the flat `estimated_hours * cost_per_hour` path, not the approval path a draft comment claimed
2. The `strategy` command's sample output was invented, not run - replaced with real output from an actual reallocation (`engineering -> research: $2,352.94`, verified against a genuine 95%-utilized `research` department)

### 🔧 Design Decisions

- **Document the real DI/shared-singleton split plainly, not just "run the tests."** The Testing section explains *why* some tests use isolated instances and others deliberately don't (from the previous checkpoint's reasoning) - a future contributor reading only the README, not eighteen `DAILY_PROGRESS.md` checkpoints, should understand the pattern without archaeology.
- **Verify example output by running it, not by reasoning about what it should be.** Exactly the lesson from checkpoints 5 and 7 today, applied to documentation instead of code - a plausible-looking number is not the same as a correct one.

### ✅ Validation

- Every command shown in the README's example session was actually run in an isolated temp directory to confirm its output, including a deliberately-constructed budget imbalance to get real `strategy` output
- No code changed, so no test suite impact - confirmed `git status` shows only `README.md` modified

### 📝 Next Steps

- The other top-level docs (`SUMMARY.md`, `SESSION_SUMMARY.md`, `IMPROVEMENTS.md`, `QUICKSTART.md`, `UPGRADE_GUIDE.md`, `TEST_REPORT.md`) are likely similarly stale (several predate `main_v2.py` too) - not addressed today; `README.md` was the one a new reader would hit first
- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically
- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones (deliberately left as-is)

### 📂 Files Modified

- `README.md` (full rewrite)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TWENTIETH CHECKPOINT TODAY: FINISHING THE DOCUMENTATION SWEEP

**Summary:** The previous checkpoint's Next Steps flagged the other top-level docs as "likely similarly stale." Checked all seven: three (`SESSION_SUMMARY.md`, `TEST_REPORT.md`, `IMPROVEMENTS_APPLIED.md`) are honestly dated snapshots ("Date: 2026-09-07/08") that don't claim to describe the current system, so their staleness is benign, same as any past `DAILY_PROGRESS.md` entry - left untouched. Two (`QUICKSTART.md`, `SUMMARY.md`) presented undated, "what you have"/evergreen framing while actually describing `main.py` and Windows `.bat` launchers that don't exist. One (`IMPROVEMENTS.md`) is a legitimate historical design-rationale document (mirroring `UPGRADE_GUIDE.md`, left alone) but still had a "Current:" code example that could mislead a reader taking it at face value.

### ✅ What Changed

**`QUICKSTART.md`** — rewritten in full, same treatment as `README.md`: every `main.py` → `main_v2.py`, the "Files" list updated to the real current module set (`budgets.py`, `workflows.py`, `strategy.py`, `performance.py`, `agent_state.py`), and the architecture diagram updated to mention capacity/budget/decision checks instead of the old "5 staff members simulate" description. Kept intentionally short - a quickstart doesn't need README's depth, just correct commands.

**`SUMMARY.md`** — added a banner at the top marking it a historical day-one snapshot (it references `main.py`, `task_executor.py`, and Windows `.bat` files that don't exist, plus a roadmap superseded by everything actually built since), pointing to `README.md`/`QUICKSTART.md` for current docs and `DAILY_PROGRESS.md` for history. Body left otherwise unedited - not worth fully rewriting a document that substantially duplicates `README.md`'s purpose; two "current state" documents to keep in sync forever is worse than one clearly-marked historical one.

**`IMPROVEMENTS.md`** — same banner treatment: marked as the original v1→v2 design proposal (its "Current:" examples describe pre-`_v2` behavior), pointing forward to `README.md`/`DAILY_PROGRESS.md`.

### 🔧 Design Decisions

- **Not every stale doc gets a full rewrite.** `README.md`/`QUICKSTART.md` are what a new reader opens first and are meant to be followed literally - they earned the full rewrite. `SUMMARY.md`/`IMPROVEMENTS.md` are better treated as historical records once something else (README) is the accurate reference - a clear banner fixes the misleading part without taking on permanent duplicate-maintenance debt.
- **A date in the title is what makes staleness honest rather than misleading.** `SESSION_SUMMARY.md`/`TEST_REPORT.md`/`IMPROVEMENTS_APPLIED.md` all needed zero changes for exactly this reason - nobody reads a dated snapshot expecting it to be current.

### ✅ Validation

- No code changed; `git status` confirms only the four `.md` files touched
- `QUICKSTART.md`'s commands and claims mirror the previous checkpoint's already-verified `README.md` content (effort-estimate heuristic, `status`/`report` output, architecture pipeline)

### 📝 Next Steps

- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically
- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones (deliberately left as-is)
- `main_v2.py`'s task pipeline and `workflows.py`'s workflow pipeline remain two separate systems for anything beyond a single `execute_step()` call

### 📂 Files Modified

- `QUICKSTART.md` (full rewrite)
- `SUMMARY.md` / `IMPROVEMENTS.md` (historical-snapshot banners added)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TWENTY-FIRST CHECKPOINT TODAY: A REAL BUG FOUND BY REVIEWING, NOT BUILDING

**Summary:** Twenty checkpoints of rapid feature work is a lot of surface area to have never deliberately re-read for correctness. Switched modes: instead of the next feature, did a targeted review of `workflows.py`'s budget-reservation logic - the highest-risk, most-composed piece of code from today (touched by checkpoints 2, 8, 9, 10, 11, and 16). Found a real, serious bug on the first thing checked.

### 🐛 The Bug

`WorkflowEngine.get_next_step()` re-runs its entire reservation logic every time it's called, with no check for whether the step it's about to "advance to" is already `IN_PROGRESS` from a prior call. Calling it a second time on the same still-pending step - e.g. a user simply re-running `workflow next <instance_id>` to remind themselves what's next, never having advanced anything - re-attempted `reserve_funds()` for the step's full `estimated_cost` a second time. Since the *existing* reservation is already subtracted out of `DepartmentBudget.available()`, that second attempt failed for lack of double the headroom, and the step was wrongly flipped to `BLOCKED` with the whole workflow `ESCALATED` - corrupting a perfectly healthy, affordable step into a stuck one, with no code change or budget event to explain why.

Found by manually probing the exact call pattern ("what happens if I call this twice") rather than by any test - every one of today's existing workflow tests happened to call `get_next_step()` exactly once per step before completing or approving it, so the bug had zero test coverage despite `get_next_step()` being exercised by six separate checkpoints.

### 🐛 A Second, Related Finding: `WorkflowEngine` Had No Budget Injection

While isolating a test to investigate the bug cleanly, discovered `WorkflowEngine` never got the dependency-injection treatment `DepartmentHeadAgent`/`TaskExecutor` got in checkpoint 9 - `get_next_step()`/`approve_step()`/`retry_blocked_step()` all hardcoded the shared global `budget_manager`. This meant `test_workflow_budget.py`/`test_workflow_retry.py`'s "isolated" `WorkflowEngine(data_file=...)` instances were never actually isolated on the budget side - only the workflow-instance data was isolated; every dollar amount in those tests was silently being read from and written to the real shared `data/budgets.json`. My initial bug-reproduction attempt used an isolated `BudgetManager` I'd constructed myself but never actually wired in, which is exactly what made the bug invisible on the first (flawed) probe - it silently checked the wrong object.

### ✅ What Changed

**`workflows.py`**
- `WorkflowEngine.__init__` gains `budget_manager=None`, the same DI pattern as everything else today; every internal `budget_manager.` call now goes through `self.budget_manager.`
- `get_next_step()`: if the computed next step's status is already `IN_PROGRESS`, return it immediately without touching the budget at all - fixes the bug directly

**`test_workflow_budget.py` / `test_workflow_retry.py`**
- Now construct and inject their own isolated `BudgetManager`, closing the gap that made them look isolated without actually being isolated

**`test_workflow_idempotent_advance.py` (new)** — three scenarios, all passing and idempotent:
1. Five repeated `get_next_step()` calls on the same step leave the reservation at exactly its original amount the whole time - never grows, never collapses to zero, never blocks
2. The step still approves normally afterward, converting the (undisturbed) reservation to real spend
3. Confirms the injected `BudgetManager` is what actually gets mutated, and the shared global singleton is never touched - the exact isolation check the first (flawed) probe got wrong

### ✅ Validation

- `python -m py_compile` clean
- The exact failure was reproduced first (properly isolated this time), confirmed by the fix, then re-verified through the *real* CLI: `workflow next` called three times in a row on a feature-request's "Design Specification" step stayed stable and still approved cleanly afterward
- `test_workflow_idempotent_advance.py`: all 3 scenarios pass, run twice back-to-back
- Full suite (20 test files now): all pass

### 📝 Next Steps

- The same "called twice" review angle is worth applying to other today's-built call paths (e.g. `DepartmentHeadAgent.run()` called concurrently for the same agent, `retry_blocked_step()` called while a retry is already in flight) rather than assuming today's other checkpoints are equally clean
- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically
- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones (deliberately left as-is)

### 📂 Files Modified

- `workflows.py` (budget-manager DI, idempotent `get_next_step()` fix)
- `test_workflow_budget.py` / `test_workflow_retry.py` (now inject an isolated `BudgetManager`)
- `test_workflow_idempotent_advance.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TWENTY-SECOND CHECKPOINT TODAY: A SECOND, MORE SERIOUS CONCURRENCY BUG

**Summary:** The previous checkpoint's own Next Steps said to apply the same "called twice" review angle elsewhere rather than assume the rest of today's work was equally clean. Took that literally and looked at `TaskExecutor.execute_parallel()` - the one feature that predates today entirely (Day 1's "parallel execution") but which today's checkpoints (budget spend, workload tracking, analytics recording) gave real, observable failure consequences for the first time. Found a second, more serious bug: silent data loss under concurrent load, with zero errors reported to the caller.

### 🐛 The Bug

`TaskExecutor.get_agent_for_department()` caches one `DepartmentHeadAgent` per department and reuses it across every worker thread in the pool. Two tasks routed to the *same* department in the same `execute_parallel()` batch therefore call `run()` on the exact same agent object concurrently. Every mutation inside `run()` - `self.agent_state.add_task()`/`remove_task()`, the budget check and spend, `agent_state.record_performance()`, `analytics.record_task()` - is a non-atomic read-modify-write on state shared by every thread touching that department, with no lock anywhere.

Confirmed with a concrete repro: 30 tasks submitted to one department, processed with 8 worker threads. `execute_parallel()` reported **zero errors** - every task individually "succeeded" - but `analytics.get_agent_metrics()` showed only **28** `tasks_completed`, and the department's budget was short by exactly the two missing tasks' cost. Two completions' bookkeeping vanished into a lost-update race with no trace of failure anywhere a caller would see.

### ✅ What Changed

**`task_executor_v2.py`**
- `DepartmentHeadAgent.__init__` gains `self._lock = threading.Lock()`
- `run()` is now a thin wrapper that acquires the lock and delegates to a new `_run_locked()` holding the original body - same fix as checkpoint 21's `get_next_step()`, but here the fix is "serialize this agent's work" rather than "make this call idempotent," since the correctness problem is genuinely about protecting one agent's mutable state across threads

**`test_concurrent_same_department.py` (new)** — three scenarios, all passing and idempotent:
1. 30 concurrent same-department tasks (8 workers) record exactly 30 completions and exactly `30 * hourly_rate` spent - not fewer, and workload settles back to exactly `0`
2. The same stress scenario repeated three times stays correct each time - a race that only shows up occasionally would still be a real bug, so "passed once" wasn't good enough
3. Four different-department tasks still complete well under a wall-clock bound that would only be possible if they ran concurrently, confirming the fix serializes only *same*-department work and doesn't collapse the whole feature into sequential execution

### 🔧 Design Decisions

- **Whole-method lock, not fine-grained locks scattered across `agent_state.py`/`budgets.py`/`performance.py`.** All the at-risk mutations funnel through this one method for a given agent; locking at that single choke point guarantees nothing is missed, at the cost of serializing a real (non-mock) LLM call's network latency for same-department tasks too. That's the correct tradeoff: correctness over a micro-optimization that would need three separate modules' internals touched and re-verified to get right, and different-department tasks - the common case - are unaffected.
- **Verified the fix doesn't just fix task 1 - reran the exact repro three times, plus a wall-clock check that cross-department parallelism survived.** The stress test is randomized-timing-dependent by nature; a single passing run proves less than three, and "the bug is gone" isn't the same claim as "concurrent code still runs concurrently."

### ✅ Validation

- `python -m py_compile` clean
- The exact 30-task repro re-run three times after the fix: `tasks_completed`, `workload`, and `spent` exactly correct every time (previously non-deterministic and short)
- Re-verified through the real CLI: 10 tasks submitted to `engineering` in one batch, processed in parallel, `report` showed exactly `Total Tasks: 10` and `Budget Spent: $2,100.00` (`10 * $210/hr`, `engineering_head`'s real config-derived rate) - no loss
- `test_concurrent_same_department.py`: all 3 scenarios pass, run twice back-to-back
- Full suite (21 test files now): all pass

### 📝 Next Steps

- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically
- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones (deliberately left as-is)
- Two real bugs found by deliberate review in two consecutive checkpoints suggests a third pass is worth doing before assuming the review is complete - `agent_decisions.py`'s `find_best_delegate()` and `OrganizationDecisionMaker` haven't been looked at with this lens yet

### 📂 Files Modified

- `task_executor_v2.py` (per-agent lock around `run()`)
- `test_concurrent_same_department.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TWENTY-THIRD CHECKPOINT TODAY: REVIEWING agent_decisions.py

**Summary:** Continuing the review thread from the previous two checkpoints' Next Steps, read `agent_decisions.py` end to end - `AgentDecisionEngine.decide()`, `find_best_delegate()`, and `OrganizationDecisionMaker` - none of which had been looked at with a "find the bug" lens today, only exercised through their existing tests. One clear, safe cleanup; two findings documented but deliberately not changed, because "fix" would mean guessing at intended business logic rather than correcting an objectively wrong value.

### ✅ What Changed

**`agent_decisions.py`**
- `decide()`'s final `DecisionResult` had `decision="execute" if not needs_approval else "execute"` - both branches of that ternary produce the identical string. Simplified to `decision="execute"`. Purely cosmetic: `approval_required=needs_approval` (a separate field, correctly set) is what `task_executor_v2.py`'s `run()` actually branches on, so this never affected behavior - confirmed by re-running the full suite before and after with identical results.

### 🔍 Two Findings, Documented But Not Changed

- **`should_execute()` returning `False` with no available delegate silently falls through to executing anyway.** `decide()`'s "can't execute at all" branch explicitly escalates when `find_best_delegate()` returns nothing; the "can execute but shouldn't" branch has no matching `else` - if there's no one to delegate to, control just falls past the `if` into the "we're executing" section below, producing an ordinary `execute` decision with no trace that the agent's own preference said no. This could be a bug (the two branches are asymmetric for no clear reason) or could be the intended real-world fallback ("nobody else can take it, so do it despite low affinity or a full plate") - it's genuinely ambiguous without knowing which the original design meant, and either fix (add an escalate `else`, or leave it and just note it in the reasoning string) changes actual decision outcomes rather than correcting a value that's obviously wrong. Left alone rather than guessed at.
- **`can_execute()`'s `needs_approval_over_50k` constraint checks `context.task_id.startswith("high_budget")`** - a string-prefix convention from Day 1's test fixtures. `task_executor_v2.py`'s real `decide_on_task()` constructs `task_id` as `f"task_{int(time.time())}"`, which never matches that prefix, so this constraint is permanently dead in the actual running system - real budget enforcement happens entirely through today's `budgets.py`/`BudgetManager` path instead. Not fixed (removing dead code that predates today isn't the same kind of low-risk cleanup as the ternary above, and the constraint mechanism itself - `AgentProfile.constraints` as free-text strings matched by convention - is a broader design question, not a one-line correction).

### 🔧 Design Decisions

- **Not every finding from a review pass gets changed.** The previous two checkpoints fixed confirmed bugs with objectively wrong output (lost data, corrupted state) where the correct behavior was unambiguous. These two findings are different in kind - correcting them means choosing an intended behavior the original author never wrote down, which is a design decision for whoever owns this system, not a bug fix. Recording them clearly here is more honest than picking one interpretation and shipping it as if it were obviously right.

### ✅ Validation

- `python -m py_compile` clean
- Full suite (21 test files) re-run before and after the one change, byte-for-byte identical pass results, confirming the ternary simplification changed nothing observable

### 📝 Next Steps

- The two documented findings above are decisions for a human to make, not further autonomous fixes
- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically (this review pass reconfirms `OrganizationDecisionMaker` is exercised only by tests, never by the real system)
- `workflow_next()` still only shows the next step rather than auto-executing non-approval ones (deliberately left as-is)

### 📂 Files Modified

- `agent_decisions.py` (dead ternary simplified)
- `DAILY_PROGRESS.md` (this report)

---

## 🎯 TWENTY-FOURTH CHECKPOINT TODAY: THE CONCURRENCY FIX HAD ITS OWN RACE CONDITION

**Summary:** Stress-tested checkpoint 22's own fix rather than trusting a single clean run - and it wasn't actually clean. Running the exact 15-department, 5-tasks-each concurrent load repeatedly (not once) failed roughly 1 in 4 times, in a way checkpoint 22's per-agent lock should have prevented. The lock itself was correct; what wasn't protected was *creating* the locked object in the first place.

### 🐛 The Bug

`TaskExecutor.get_agent_for_department()` caches one `DepartmentHeadAgent` per department with a plain `if department not in self.agents: self.agents[department] = DepartmentHeadAgent(...)` - classic check-then-act, no lock. Two worker threads racing to process the *first* task ever routed to a brand-new department can each see "not cached yet" and each construct their own separate `DepartmentHeadAgent` - each with its own separate `threading.Lock()` from checkpoint 22. Two objects for "the same" department don't share a lock, so that fix did nothing to protect them from each other. `AgentRegistry.register()` had the identical check-then-act pattern one layer down, for the same reason.

Confirmed by running the checkpoint-22 repro (30 same-department tasks) many times cleanly, then testing a *different* shape - 15 brand-new departments, 5 tasks each, 16 workers - which failed about 1 run in 4: a department's own recorded completions short by exactly the number of racing duplicate-agent creations, or the shared `expense_log` missing an entry.

### ✅ What Changed

**`task_executor_v2.py`**
- `TaskExecutor.__init__` gains `self._agents_lock = threading.Lock()`
- `get_agent_for_department()` now uses double-checked locking: the fast path (agent already cached) never touches the lock; only first-creation re-checks inside it

**`agent_state.py`**
- `AgentRegistry.__init__` gains `self._register_lock = threading.Lock()`; `register()` gets the same double-checked-locking treatment, for defense in depth at the actual source of the shared state - not just the one call path (`DepartmentHeadAgent.__init__`) that happens to be protected transitively by the `TaskExecutor` fix today

**`test_concurrent_first_creation.py` (new)** — two scenarios, all passing and idempotent:
1. The 15-brand-new-departments/5-tasks-each/16-workers scenario run 5 times in a row: every run gets exactly 75 total completions, exactly 75 `expense_log` entries, and - checked individually, not just as an aggregate - every single department gets exactly its own 5, not just the right sum
2. 50 concurrent calls to `get_agent_for_department()` for one never-before-seen department, checked directly: exactly one `DepartmentHeadAgent` object (by identity, not just by department name) is ever created

### 🔧 Design Decisions

- **Distrust a single clean run of a concurrency fix.** Checkpoint 22's fix was correct as far as it went and passed its own test every time - the bug it missed was in a different part of the same code path, only visible under a different load shape (many first-time departments, not many repeats of one). "The lock exists and the test passes" isn't the same claim as "this is race-free" - only running many trials of several different shapes earns that.
- **Double-checked locking, not a lock held for the whole method.** The common case (an already-cached agent, which is nearly every call after the first) never touches the lock at all; only the narrow first-creation window pays for it.
- **Fixed it at both the call site and the source.** `TaskExecutor`'s fix alone would have covered the one path exercised today, but `AgentRegistry.register()`'s identical unprotected pattern is a landmine for any future caller that doesn't happen to go through `TaskExecutor` first - fixed there too rather than declaring it out of scope.

### ✅ Validation

- `python -m py_compile` clean
- The exact bug reproduced first (roughly 1-in-4 failure rate across 15 manual runs), then the fix verified against 15 consecutive clean runs of the same load with zero failures
- `test_concurrent_first_creation.py`: both scenarios pass, run twice back-to-back
- Full suite (22 test files now): all pass

### 📝 Next Steps

- A third stress shape worth trying before assuming this area is now clean: many *different* department names created concurrently while an *existing* department is simultaneously under heavy same-department load, mixing both race windows in one run
- No execution path exists for `ceo`/`tech_lead`/`product_coordinator` roles specifically
- The two `agent_decisions.py` findings from the previous checkpoint remain open design questions, not further autonomous fixes

### 📂 Files Modified

- `task_executor_v2.py` (`TaskExecutor._agents_lock`, double-checked locking in `get_agent_for_department()`)
- `agent_state.py` (`AgentRegistry._register_lock`, double-checked locking in `register()`)
- `test_concurrent_first_creation.py` (new)
- `DAILY_PROGRESS.md` (this report)

---

# Daily Progress Report - September 9, 2026

## 🎯 PHASE 3 (RESOURCES): BUDGET & CAPACITY MANAGEMENT

**Summary:** Day 2 implementation. Phases 1, 2, and 4 (agent specialization, workflows, analytics) were already in place but agents could execute, delegate, and get approved for work as if money and headcount were unlimited. Today's work grounds those decisions in finite resources: department budgets that can run out, and staffing capacity that can be over- or under-utilized.

### ✅ What Was Built

**`budgets.py` (~275 lines) — new module**

- `ExpenseRecord`: a single committed spend (department, amount, category, task/agent references, timestamp)
- `DepartmentBudget`: allocation vs. spend vs. *reservations* for one department
  - `available()` / `utilization_pct()` / `can_afford()`
  - `reserve()` / `release_reservation()` / `commit_reservation()` — lets a workflow approval gate hold funds before they're actually spent, so two concurrent requests can't both pass a check against the same uncommitted dollars
- `BudgetManager`: registry + JSON persistence (`data/budgets.json`), mirroring the pattern in `agent_state.AgentRegistry` and `performance.PerformanceAnalytics`
  - `allocate()`, `request_expense()`, `reserve_funds()`, `confirm_reservation()`, `cancel_reservation()`
  - `approve_decision(decision, department)`: bridges `agent_decisions.DecisionResult` into a real budget check instead of the mocked `budget_available: float` the CEO's `OrganizationDecisionMaker.approve_decision()` currently takes
  - `organization_summary()`, `over_budget_departments()` for org-wide rollups and alerts
- `CapacitySnapshot` / `CapacityManager`: reads the *existing* `agent_registry` (no new state to keep in sync) to report per-department headcount vs. workload
  - `snapshot()`, `is_over_capacity()`, `organization_report()`
  - `recommend_actions()`: flags departments to hire into (over-utilized) or reassign work out of (idle), same shape as `performance.get_recommendations()`

**`config.json`**
- Added `monthly_budget` to each department (engineering $50k, sales $35k, design $30k, support $25k, research $20k) so `BudgetManager` has real starting allocations to seed from instead of requiring manual setup.

**`test_budgets.py` (~165 lines)** — five scenarios, all passing:
1. Allocation, spending, and rejecting a request that would overspend
2. Reserve → confirm / the reservation blocking a second request that would exceed what's left
3. Organization-wide summary and the over-budget-department alert
4. A `DecisionResult` requiring approval getting checked against a real department budget
5. Capacity snapshot + recommendation on an isolated agent registry (busy vs. idle agent)

### 🔧 Design Decisions

- **Reservations, not just check-then-spend.** A naive `if amount <= available: spend()` has a race between an agent's decision and a leader's approval. Modeling reservations as first-class (`DepartmentBudget.reserved`) means a workflow's approval step can hold funds the moment a request is made, not just when it's granted.
- **Capacity reads agent_state, doesn't duplicate it.** `CapacityManager` computes snapshots from `agent_registry.all()` (`max_concurrent_tasks`, `current_workload`) rather than tracking a second copy of headcount — one source of truth for "how busy is this agent."
- **Test isolation.** `test_budgets.py` uses its own data files (`data/test_budgets.json`, `data/test_agent_states.json`, cleaned up after each run) instead of the shared `data/agent_states.json`/`data/budgets.json`, so running the demo doesn't leave fake `capacity_test` agents or throwaway ledgers in the committed state.
- **Left `agent_decisions.py` untouched.** Rather than changing `OrganizationDecisionMaker.approve_decision()`'s signature (which `test_agent_decisions.py` already exercises with a raw float), `BudgetManager.approve_decision()` is an additive bridge — lower risk, and the two can be reconciled once budgets are wired into the actual task execution path.

### ✅ Validation

- `python -m py_compile` clean on all `.py` files in the repo
- `python3 test_budgets.py`: all 5 scenarios pass
- Re-ran `test_agent_decisions.py`, `test_performance.py`, `test_workflows.py`: still pass, confirming nothing in the existing system was touched

### 📝 Next Steps

- Wire `BudgetManager`/`CapacityManager` into `task_executor_v2.py` so department heads actually check budget/capacity before executing or delegating (currently `budgets.py` is a standalone, tested module — not yet called from the execution path)
- Use `reserve_funds()`/`confirm_reservation()` inside `workflows.py`'s existing "Budget Approval" step instead of the current no-op approval gate
- Reconcile `OrganizationDecisionMaker.approve_decision()`'s mocked cost/budget with `BudgetManager.approve_decision()`
- Add a spend/capacity view to `performance.generate_report()` so a single report covers quality, cost, and resource utilization

### 📂 Files Modified

- `budgets.py` (new)
- `test_budgets.py` (new)
- `config.json` (added `monthly_budget` per department)
- `DAILY_PROGRESS.md` (this report)

---

# Daily Progress Report - September 8, 2026

## 🎯 PHASE 1 & 2 FOUNDATION: COMPLETE

**Summary:** Day 1 implementation of agent specialization, autonomous decision-making, workflow orchestration, and performance analytics. System now has foundational architecture for autonomous business simulation.

### ✅ Major Components Delivered

#### Phase 1: Agent Specialization

1. **agent_state.py** (~250 lines)
   - `AgentProfile`: Defines agent archetype, expertise, capabilities, constraints
   - `PerformanceMetrics`: Tracks quality, speed, cost, error rate, satisfaction
   - `RelationshipScore`: Trust scores and collaboration history with other agents
   - `AgentState`: Complete agent including profile, metrics, relationships, workload
   - `AgentRegistry`: Persistent registry for all agents with load/save

2. **agent_decisions.py** (~200 lines)
   - `DecisionContext`: Task information requiring decision
   - `DecisionResult`: Decision with reasoning and confidence
   - `AgentDecisionEngine`: Autonomous decision-making logic
     - `can_execute()`: Capability and capacity checks
     - `should_execute()`: Preference and affinity-based decisions
     - `find_best_delegate()`: Ranking algorithms for task delegation
     - `requires_approval()`: Determines approval requirements
     - `decide()`: Main decision workflow
   - `OrganizationDecisionMaker`: Leadership-level decision approval

3. **task_executor_v2.py** (Enhanced - ~200 additions)
   - Integrated AgentState with task execution
   - Autonomous decision-making in DepartmentHeadAgent
   - Performance tracking per task (quality, cost, time)
   - Workload management and availability tracking
   - Agent learning preference updates
   - Full test suite demonstrating decisions

#### Phase 2: Workflow Orchestration

4. **workflows.py** (~400 lines)
   - WorkflowTemplate: Reusable workflow definitions
   - WorkflowInstance: Running instances with state
   - WorkflowStep: Individual tasks with dependencies
   - WorkflowEngine: Orchestrates multi-step processes
   - Approval gates and step sequencing
   - Pre-built templates:
     - Feature Request (7-step process)
     - Bug Fix (4-step fast track)
   - Full status tracking and rejection handling

#### Phase 4: Performance Analytics

5. **performance.py** (~350 lines)
   - AgentMetrics: Per-agent performance tracking
   - DepartmentMetrics: Aggregated department stats
   - SystemMetrics: Organization-wide KPIs
   - PerformanceAnalytics: Analysis engine
   - Top performer ranking by multiple metrics
   - Automatic recommendations for optimization
   - JSON persistence and trend analysis

#### Configuration

6. **config.json - Agent Definitions**
   - 8 agent profiles defined:
     - **LeaderAgent**: CEO (strategy, budget allocation)
     - **ManagerAgents**: 5 department heads (delegation, quality control)
     - **SpecialistAgent**: Tech lead (architecture, mentoring)
     - **CoordinatorAgent**: Product coordinator (cross-functional workflow)
   - Expertise areas, capabilities, constraints, capacity limits

### 📊 Comprehensive Metrics

| Metric | Value |
|--------|-------|
| **Files Created** | 5 new core modules |
| **Lines of Code** | 1,400+ lines |
| **Test Files** | 3 comprehensive test suites |
| **Code Quality** | Clean, well-documented, typed |
| **Compilation** | ✅ 100% pass |
| **Test Coverage** | All major features demonstrated |
| **Git Commits** | 2 commits (2 kB total) |
| **Time Spent** | ~2.5 hours |

**System Components Implemented:**
- Agent specialization architecture ✅
- Autonomous decision-making engine ✅
- Workflow orchestration engine ✅
- Performance tracking & analytics ✅
- Integration with existing task executor ✅
- Multi-agent testing framework ✅

### 🔧 Technical Details

**Key Features Implemented:**
- Dataclass-based state management (immutable, serializable)
- JSON persistence for agent profiles and metrics
- Performance tracking with running averages
- Relationship trust scores with collaboration history
- Skill-level matching and workload constraints
- Multi-factor decision ranking (skill, affinity, workload, trust)
- Support for agent types: Leader, Manager, Specialist, Coordinator

**Design Decisions:**
- Used dataclasses for clean, type-safe state objects
- Registry pattern for agent discovery and persistence
- Affinity learning as +1 to -1 scale for task type preferences
- Approval gates based on skill level, complexity, and cost thresholds
- Delegation algorithm ranks candidates by multiple factors (not just skill)

### 🔗 Integration Points

- **core.py**: Will integrate AgentRegistry with EventBus
- **task_executor_v2.py**: Will use AgentDecisionEngine for task routing
- **main_v2.py**: CLI will load agents from registry
- **llm_provider.py**: Agent skill levels will influence provider selection

### 📝 Phase 1 - Day 2 Update: INTEGRATION COMPLETE

**Completed:**
1. ✓ Integrated AgentState with task_executor_v2.py
2. ✓ Updated DepartmentHeadAgent to use decision engine
3. ✓ Added performance tracking to task execution
4. ✓ Created comprehensive test suite (test_agent_decisions.py)
5. ✓ Validated end-to-end autonomous decisions

**New Features:**
- DepartmentHeadAgent now makes autonomous decisions (execute/delegate/escalate)
- Performance metrics tracked per task execution
- Agent learning preferences updated based on results
- Workload management integrated
- Relationship trust system working
- Full test demonstration of all systems

**Test Results:**
- Agent profile registration: PASS
- Decision-making scenarios: PASS
- Performance tracking: PASS
- Delegation logic: PASS
- Relationship management: PASS
- JSON persistence: PASS

### 📝 Next Steps (Tomorrow)

**Phase 1 - Day 3:**
1. Integrate agent learning from existing task results
2. Build approval workflow integration with TaskExecutor
3. Test multi-agent delegation chains
4. Create performance analytics dashboard

### 💡 Key Achievements

✨ **Agents now have:**
- Identity and expertise profiles
- Memory of past performance
- Learned preferences for task types
- Relationships and trust with other agents
- Capacity constraints and availability tracking
- Autonomous decision-making capability

🧠 **Decision logic enables:**
- Smart delegation based on multiple factors
- Approval routing based on complexity/cost
- Escalation when no suitable agent found
- Learning from task results over time

### ⚠️ Known Gaps (Addressed Tomorrow)

- Agent initialization in existing system
- Tie-in with EventBus for decision logging
- Task result feedback loop for performance updates
- Relationship updates after collaboration

---

**Status:** ✅ **COMPLETE** - Phase 1 foundation established

**Next Session:** Integrate with existing task executor and test autonomous decisions

**Commit Message:** `[PHASE-1] Agent specialization foundation: archetypes, state management, decision engine`
