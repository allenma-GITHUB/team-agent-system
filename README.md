# Team Agent System

A multi-department AI task management system that simulates an autonomous
business: department heads with real seniority-based compensation, budgets
that actually run out, staffing capacity that can be over- or under-utilized,
multi-step approval workflows, and org-wide performance/resource reporting.
Runs **completely offline** without API keys (full mock mode), or integrates
with multiple LLM providers when configured.

> **Entry point:** `main_v2.py`. There is no `main.py` in this repo - the
> system evolved past its original single-file version; `main_v2.py` is the
> only CLI.

## Features

- **Works without API keys** - full mock mode for immediate use, with graceful degradation if a configured key fails
- **Multi-department routing** - tasks auto-routed by keyword, or specify `--dept`
- **Parallel task execution** - a thread pool processes queued tasks concurrently
- **Agent specialization** - each department head has a real profile (`config.json`'s `agents` section: `agent_type`, `skill_level`, capabilities, constraints) instead of an identical generic stub
- **Seniority-based compensation** - hourly rate derives from `agent_type` and `skill_level` (a `LeaderAgent` costs more than a `CoordinatorAgent`; skill 5 costs more than skill 1) - see `AgentProfile.hourly_rate()`
- **Real budgets** - every department has a monthly budget; every task (not just big ones) draws from it, and a depleted budget blocks further work instead of executing for free
- **Capacity tracking** - staffing utilization per department, with over/under-utilization recommendations
- **Multi-step workflows** - budget-gated approval chains (`feature_request`, `bug_fix`) that persist across CLI invocations and are actually executed by department agents, not just rubber-stamped
- **Strategic budget reallocation** - move unspent budget from underspending departments to ones approaching their limit, automatically or as a reviewable dry run
- **Org-wide analytics** - a single report combining task quality/cost, budget, and capacity

## Quick Start

```bash
# No dependencies required for basic operation!
# Optional: install libraries for real LLM providers
pip install anthropic openai google-generativeai requests

# Optional: add API keys (system works fine without them - mock mode)
cp .env.example .env  # then edit .env

# Submit and process a task
python main_v2.py submit "Fix login bug"
python main_v2.py process
python main_v2.py status
```

## CLI Reference

| Command | Description |
|---|---|
| `submit <description> [--dept D] [--hours N]` | Queue a task. Department auto-routed by keyword if `--dept` omitted; `--hours` defaults to a keyword-based estimate (`estimate_hours()`) if omitted, not a flat `1.0` |
| `process [--sequential]` | Execute all queued tasks (parallel by default) |
| `list [--status S]` | List tasks, optionally filtered by status |
| `show <task_id>` | Show one task's full details and result |
| `status` | Task counts, LLM provider, and per-department budget/capacity |
| `report` | Combined quality/cost/budget/capacity report (`performance.py`) |
| `workflow list` | Show available workflow templates |
| `workflow start <template_id> [key=value ...]` | Create and start a workflow instance |
| `workflow next <instance_id>` | Show the next step (and its cost/approval requirements) |
| `workflow complete <instance_id> <step_id>` | Actually execute the step through its department and record the real result |
| `workflow approve <instance_id> <step_id> [--reject]` | Approve or reject a step requiring sign-off |
| `workflow retry <instance_id>` | Retry a step that's `BLOCKED` on insufficient budget |
| `workflow status <instance_id>` | Full status of one workflow instance |
| `strategy [--apply]` | Show (and optionally apply) proposed budget reallocations across departments |

### Example session

```bash
$ python main_v2.py submit "Fix a typo in the header" --dept engineering
✓ Task 0001 submitted to ENGINEERING (est. 0.5h)   # low-effort keyword -> short estimate

$ python main_v2.py submit "Full platform migration" --dept engineering
✓ Task 0002 submitted to ENGINEERING (est. 16.0h)  # high-effort keyword -> long estimate

$ python main_v2.py process
🚀 Processing 2 task(s)...
✓ Task 0001 [ENGINEERING ] completed
✓ Task 0002 [ENGINEERING ] completed   # both billed for their own hours, not a flat rate

$ python main_v2.py status
💰 Budget & Capacity:
  Engineering     budget: $3,465/$50,000 spent (7%)   capacity: 0/4 tasks (0%)

$ python main_v2.py workflow start feature_request title="Dark mode"
✓ Started 'Feature Request Process' -> instance feature_request_...

$ python main_v2.py workflow next feature_request_...
→ Next step: Design Specification (design) [needs approval: design_lead] [$3,000 from design]

$ python main_v2.py workflow complete feature_request_... design   # actually runs design_head
✓ Step 'design' executed and marked complete

$ python main_v2.py workflow approve feature_request_... design    # commits the $3,000 reservation
✓ Step 'design' approved

$ python main_v2.py strategy   # (after research separately ran up 95% budget utilization)
💡 Proposed Reallocations (1):
  engineering -> research: $2,352.94
    engineering at 7% utilization (underspending) -> research at 95% utilization (approaching limit)
```

## Departments & Agents

Both are defined in `config.json`. Departments control keyword routing, staff
count, and monthly budget:

```json
"departments": {
  "engineering": {
    "keywords": ["code", "bug", "feature", "deploy"],
    "staff": 5,
    "monthly_budget": 50000
  }
}
```

The `agents` section defines specific profiles by id (`f"{department}_head"`,
e.g. `"engineering_head"`) - `agent_type`, `skill_level`, `capabilities`,
`constraints`, `max_concurrent_tasks`. A department not named here still
works; its head falls back to a generic `ManagerAgent`/skill-3 profile.

```json
"agents": {
  "engineering_head": {
    "type": "ManagerAgent",
    "skill_level": 4,
    "capabilities": ["code_review", "architecture_design"],
    "max_concurrent_tasks": 4
  }
}
```

## Architecture

| Module | Responsibility |
|---|---|
| `main_v2.py` | CLI entry point |
| `core.py` | `EventBus` (tracing), base `Registry`/`BaseAgent` |
| `departments.py` | Department config, keyword routing, effort-hour heuristic |
| `agent_state.py` | `AgentProfile` (identity, compensation), `AgentState`, `AgentRegistry` |
| `agent_decisions.py` | `AgentDecisionEngine` (execute/delegate/escalate), `OrganizationDecisionMaker` |
| `task_executor_v2.py` | `DepartmentHeadAgent` (runs a task through capacity/decision/budget gates), `TaskExecutor` (sequential/parallel) |
| `budgets.py` | `BudgetManager` (allocate/spend/reserve), `CapacityManager` (staffing utilization) |
| `workflows.py` | `WorkflowEngine` - persisted, budget-gated, multi-step approval chains, bridged to real execution via `execute_step()` |
| `strategy.py` | `StrategicPlanner` - cross-department budget reallocation |
| `performance.py` | `PerformanceAnalytics` - quality/cost/success metrics plus a combined resource (budget+capacity) report |
| `llm_provider.py` | Routes to Anthropic/OpenAI/Gemini/Groq/Nvidia if configured, else mock |

Every stateful module (`agent_state.py`, `budgets.py`, `workflows.py`,
`performance.py`) persists to its own file under `data/` and accepts
constructor-level dependency injection (an isolated `AgentRegistry`,
`BudgetManager`, etc.) so callers - tests, in particular - don't have to
share global state. The module-level singletons (`budget_manager`,
`workflow_engine`, `analytics`, ...) are what the CLI uses by default.

## How a task actually runs

```
submit → queued in data/tasks.json
process → TaskExecutor.execute(department, description, estimated_hours)
  → DepartmentHeadAgent.run():
      1. capacity_check   - snapshot the department's staffing utilization
      2. decide_on_task() - AgentDecisionEngine: execute / delegate / escalate
      3. budget check     - approval-required tasks go through approve_decision();
                             everything else still draws estimated_hours * cost_per_hour
      4. (LLM call, mock or real)
      5. record_performance() + analytics.record_task() - both metrics systems updated
```

A depleted department budget or an unavailable decision path escalates the
task (`status: "escalated"`) instead of running it - nothing executes for
free, and nothing runs work no one approved the resources for.

## Testing

Every feature has a corresponding `test_*.py` script - run any of them
directly (`python test_budgets.py`) or as a suite. Tests either construct
fully isolated dependencies (an `AgentRegistry`/`BudgetManager`/etc. pointed
at a throwaway `data/test_*.json` file, cleaned up in `main()`) or - for a
handful of Day-1 tests whose whole point is exercising persistence - the
shared global singletons deliberately. No test suite requires a network
connection or an API key; the mock LLM provider is always available.

## Extending the System

- **New department**: add an entry to `config.json`'s `departments` (keywords, staff, `monthly_budget`)
- **Richer agent profile for a department head**: add a matching entry to `config.json`'s `agents` under `f"{department}_head"`
- **New workflow template**: write a `create_*_workflow()` function in `workflows.py` returning a `WorkflowTemplate` (see `create_bug_fix_workflow()` for a minimal example), then register it in `main_v2.py`'s `WORKFLOW_TEMPLATES`
- **Custom agent class**: register it with `core.agent_registry` under `f"{department}_head"` - `TaskExecutor.get_agent_for_department()` will use it instead of the generic `DepartmentHeadAgent`

## Troubleshooting

- **Tasks not processing?** `python main_v2.py list --status queued`
- **A workflow step is stuck?** `python main_v2.py workflow status <instance_id>` - a `BLOCKED` step needs `workflow retry` after its department's budget is topped up (`strategy --apply`, or re-run after a new period)
- **API key not working?** `python main_v2.py status` shows the active LLM provider; an invalid key falls back to mock automatically

## License

MIT
