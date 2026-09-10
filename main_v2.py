#!/usr/bin/env python3
"""
Team Agent System v2 - Main CLI with Event Bus & Parallel Execution
"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from llm_provider import LLMProvider
from task_executor_v2 import TaskExecutor
from departments import DepartmentManager
from core import EventBus
from budgets import budget_manager, capacity_manager
from performance import analytics
from workflows import workflow_engine, create_feature_request_workflow, create_bug_fix_workflow
from strategy import strategic_planner

WORKFLOW_TEMPLATES = {
    "feature_request": create_feature_request_workflow,
    "bug_fix": create_bug_fix_workflow,
}

DATA_DIR = Path("data")
TASKS_FILE = DATA_DIR / "tasks.json"
RESULTS_DIR = DATA_DIR / "results"


def init_system():
    """Initialize directories and files."""
    DATA_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)

    if not TASKS_FILE.exists():
        TASKS_FILE.write_text(json.dumps([], indent=2))


def submit_task(description: str, department=None, estimated_hours: float = None):
    """Submit a new task."""
    init_system()

    tasks = json.loads(TASKS_FILE.read_text())
    task_id = str(len(tasks) + 1).zfill(4)

    if not department:
        department = DepartmentManager.route_task(description)
    if estimated_hours is None:
        estimated_hours = DepartmentManager.estimate_hours(description)

    task = {
        "id": task_id,
        "description": description,
        "department": department,
        "estimated_hours": estimated_hours,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "result": None
    }

    tasks.append(task)
    TASKS_FILE.write_text(json.dumps(tasks, indent=2))

    print(f"✓ Task {task_id} submitted to {department.upper()} (est. {estimated_hours}h)")
    return task_id


def process_tasks(parallel=True):
    """Process queued tasks with optional parallel execution."""
    init_system()

    llm = LLMProvider()
    bus = EventBus()
    executor = TaskExecutor(llm, bus=bus, max_workers=4)

    tasks = json.loads(TASKS_FILE.read_text())
    queued = [t for t in tasks if t["status"] == "queued"]

    if not queued:
        print("No queued tasks.")
        return

    print(f"\n🚀 Processing {len(queued)} task(s)...")
    if parallel and len(queued) > 1:
        print(f"   Mode: PARALLEL ({min(4, len(queued))} workers)")
    else:
        print(f"   Mode: SEQUENTIAL")

    print("-" * 80)

    start_time = time.time()

    if parallel and len(queued) > 1:
        # Parallel execution
        task_list = [(t["department"], t["description"], t.get("estimated_hours", 1.0)) for t in queued]
        results = executor.execute_parallel(task_list)

        for i, (task, result) in enumerate(zip(queued, results)):
            task["completed_at"] = datetime.now().isoformat()
            if "error" not in result:
                task["status"] = "completed"
                task["result"] = result
                print(f"✓ Task {task['id']:<4} [{task['department'].upper():<12}] completed")
            else:
                task["status"] = "failed"
                task["result"] = result
                print(f"✗ Task {task['id']:<4} [{task['department'].upper():<12}] failed: {result['error']}")
    else:
        # Sequential execution
        for task in queued:
            result = executor.execute(
                task["department"], task["description"], task.get("estimated_hours", 1.0)
            )
            task["status"] = "completed"
            task["result"] = result
            task["completed_at"] = datetime.now().isoformat()
            print(f"✓ Task {task['id']:<4} [{task['department'].upper():<12}] completed")

    total_duration = time.time() - start_time
    TASKS_FILE.write_text(json.dumps(tasks, indent=2))

    print("-" * 80)
    print(f"✓ All tasks completed in {total_duration:.2f}s")

    # Show trace summary
    bus.print_trace()


def list_tasks(status=None):
    """List tasks."""
    init_system()

    tasks = json.loads(TASKS_FILE.read_text())

    if status:
        tasks = [t for t in tasks if t["status"] == status]

    if not tasks:
        print("No tasks found.")
        return

    print(f"\n{'ID':<6} {'STATUS':<12} {'DEPARTMENT':<12} {'DESCRIPTION':<50}")
    print("-" * 85)

    for task in tasks:
        desc = task["description"][:47] + "..." if len(task["description"]) > 50 else task["description"]
        print(f"{task['id']:<6} {task['status']:<12} {task['department']:<12} {desc:<50}")


def show_task(task_id: str):
    """Show task details."""
    init_system()

    tasks = json.loads(TASKS_FILE.read_text())
    task = next((t for t in tasks if t["id"] == task_id), None)

    if not task:
        print(f"Task {task_id} not found.")
        return

    print(f"\nTask: {task['id']}")
    print(f"Status: {task['status']}")
    print(f"Department: {task['department']}")
    print(f"Description: {task['description']}")
    print(f"Created: {task['created_at']}")

    if task["result"]:
        result = task["result"]
        print(f"\nResult:")
        print(f"  Summary: {result.get('summary', 'N/A')}")
        print(f"  Analysis: {result.get('analysis', 'N/A')[:100]}...")
        if result.get('execution_steps'):
            print(f"  Staff Contributions:")
            for step in result.get('execution_steps', []):
                print(f"    - {step.get('role', 'Unknown')}: {step.get('contribution', '')}")


def show_status():
    """Show system status."""
    init_system()

    tasks = json.loads(TASKS_FILE.read_text())

    stats = {
        "total": len(tasks),
        "queued": len([t for t in tasks if t["status"] == "queued"]),
        "completed": len([t for t in tasks if t["status"] == "completed"]),
    }

    print(f"\n📊 System Status")
    print(f"{'Total Tasks':<20} {stats['total']}")
    print(f"{'Queued':<20} {stats['queued']}")
    print(f"{'Completed':<20} {stats['completed']}")

    llm = LLMProvider()
    print(f"\n🧠 LLM Provider: {llm.get_status()}")

    print(f"\n🏢 Departments:")
    for dept in DepartmentManager.get_departments():
        dept_tasks = [t for t in tasks if t["department"] == dept]
        print(f"  {dept.title():<15} {len(dept_tasks):>2} tasks")

    print(f"\n💰 Budget & Capacity:")
    for dept in DepartmentManager.get_departments():
        budget = budget_manager.get_budget(dept)
        snapshot = capacity_manager.snapshot(dept)
        if budget:
            budget_str = f"${budget.spent:,.0f}/${budget.allocated:,.0f} spent ({budget.utilization_pct():.0%})"
        else:
            budget_str = "not yet allocated"
        capacity_str = (f"{snapshot.current_workload}/{snapshot.total_capacity} tasks "
                         f"({snapshot.utilization_pct():.0%})" if snapshot.total_capacity > 0
                         else "no agents registered")
        print(f"  {dept.title():<15} budget: {budget_str:<38} capacity: {capacity_str}")

    over_budget = budget_manager.over_budget_departments()
    if over_budget:
        print(f"\n  [!] Over-budget: {', '.join(over_budget)}")


def show_report():
    """Show the full performance report: quality, cost, budget, and capacity together."""
    print(analytics.generate_report())


def init_workflows():
    """Re-register known workflow templates. They're never persisted (see
    workflows.py) - only instances are - so this must run before any
    workflow command that reads or advances an instance."""
    for factory in WORKFLOW_TEMPLATES.values():
        workflow_engine.register_template(factory())


def workflow_list():
    """List available workflow templates."""
    init_workflows()
    print("\n📋 Available Workflow Templates:")
    for template_id, factory in WORKFLOW_TEMPLATES.items():
        template = factory()
        print(f"  {template_id:<18} {template.name} ({len(template.steps)} steps)")


def workflow_start(template_id: str, input_pairs: list):
    """Create and start a new workflow instance from a template."""
    init_workflows()
    if template_id not in WORKFLOW_TEMPLATES:
        print(f"Unknown workflow template: {template_id}")
        print(f"Available: {', '.join(WORKFLOW_TEMPLATES.keys())}")
        return

    input_data = {}
    for pair in input_pairs:
        if "=" in pair:
            key, value = pair.split("=", 1)
            input_data[key] = value

    instance = workflow_engine.create_instance(template_id, input_data)
    workflow_engine.start_instance(instance.instance_id)
    print(f"✓ Started '{instance.workflow_name}' -> instance {instance.instance_id}")


def workflow_next(instance_id: str):
    """Advance to (and show) the next step of a workflow instance."""
    init_workflows()
    instance = workflow_engine.get_instance(instance_id)
    if not instance:
        print(f"No such workflow instance: {instance_id}")
        return

    step = workflow_engine.get_next_step(instance_id)
    if step:
        approval = f" [needs approval: {step.approval_role}]" if step.requires_approval else ""
        cost = f" [${step.estimated_cost:,.0f} from {step.budget_dept()}]" if step.estimated_cost > 0 else ""
        print(f"→ Next step: {step.name} ({step.owner_department}){approval}{cost}")
        print(f"  step_id: {step.step_id}")
    elif instance.status.value == "escalated":
        print(f"⚠ Blocked: {instance.error}")
        print(f"  Try: python main_v2.py workflow retry {instance_id}")
    elif instance.is_complete():
        print(f"✓ Workflow already complete (status: {instance.status.value})")
    else:
        print(f"No next step available (status: {instance.status.value})")


def workflow_complete(instance_id: str, step_id: str):
    """Mark a (non-approval) step as complete."""
    init_workflows()
    ok = workflow_engine.complete_step(instance_id, step_id, {"completed_via": "cli"})
    print(f"✓ Step '{step_id}' marked complete" if ok else f"Could not complete step '{step_id}'")


def workflow_approve(instance_id: str, step_id: str, approved: bool):
    """Approve or reject a step requiring approval."""
    init_workflows()
    ok = workflow_engine.approve_step(instance_id, step_id, approved)
    verb = "approved" if approved else "rejected"
    print(f"✓ Step '{step_id}' {verb}" if ok else f"Could not act on step '{step_id}'")


def workflow_retry(instance_id: str):
    """Retry a step that's BLOCKED on insufficient budget."""
    init_workflows()
    resumed, reason = workflow_engine.retry_blocked_step(instance_id)
    print(f"✓ Resumed: {reason}" if resumed else f"✗ Still blocked: {reason}")


def workflow_status(instance_id: str):
    """Show full status of a workflow instance."""
    init_workflows()
    status = workflow_engine.get_status(instance_id)
    if not status:
        print(f"No such workflow instance: {instance_id}")
        return

    print(f"\nWorkflow: {status['workflow_name']} ({status['instance_id']})")
    print(f"Status: {status['status']}")
    print(f"Progress: {status['progress']:.0%}")
    print(f"Current step: {status['current_step']}")
    if status["error"]:
        print(f"Error: {status['error']}")
    print("Steps:")
    for step_id, step_status in status["step_statuses"].items():
        print(f"  {step_id:<20} {step_status}")


def show_strategy(apply: bool):
    """Show (and optionally apply) CEO-level budget reallocation proposals."""
    proposals = strategic_planner.propose_reallocations()

    if not proposals:
        print("\n✓ No reallocation needed - no department is underspending enough "
              "to fund another approaching its limit.")
        return

    print(f"\n💡 Proposed Reallocations ({len(proposals)}):")
    for p in proposals:
        print(f"  {p.from_department} -> {p.to_department}: ${p.amount:,.2f}")
        print(f"    {p.reason}")

    if apply:
        strategic_planner.apply_reallocations(proposals)
        print("\n✓ Applied.")
    else:
        print("\n(Dry run - re-run with --apply to actually move the budget.)")


def main():
    if len(sys.argv) < 2:
        print("Team Agent System v2 - CLI with Event Bus & Parallel Execution")
        print("\nUsage:")
        print("  python main_v2.py submit <description> [--dept DEPARTMENT] [--hours N]")
        print("  python main_v2.py process [--sequential]")
        print("  python main_v2.py list [--status STATUS]")
        print("  python main_v2.py show <task_id>")
        print("  python main_v2.py status")
        print("  python main_v2.py report")
        print("  python main_v2.py workflow list")
        print("  python main_v2.py workflow start <template_id> [key=value ...]")
        print("  python main_v2.py workflow next <instance_id>")
        print("  python main_v2.py workflow complete <instance_id> <step_id>")
        print("  python main_v2.py workflow approve <instance_id> <step_id> [--reject]")
        print("  python main_v2.py workflow retry <instance_id>")
        print("  python main_v2.py workflow status <instance_id>")
        print("  python main_v2.py strategy [--apply]")
        print("\nExample:")
        print("  python main_v2.py submit 'Fix login bug'")
        print("  python main_v2.py process          # Parallel by default")
        print("  python main_v2.py process --sequential  # Force sequential")
        print("  python main_v2.py report           # Quality, cost, budget & capacity in one report")
        print("  python main_v2.py workflow start feature_request title='Dark mode'")
        print("  python main_v2.py strategy --apply  # Reallocate budget from idle to strained departments")
        return

    command = sys.argv[1]

    if command == "submit":
        if len(sys.argv) < 3:
            print("Usage: python main_v2.py submit <description> [--dept DEPARTMENT] [--hours N]")
            return
        desc = sys.argv[2]
        dept = None
        if "--dept" in sys.argv:
            dept = sys.argv[sys.argv.index("--dept") + 1]
        # None -> submit_task() falls back to a keyword-based estimate
        # instead of a flat 1.0h; --hours always wins if supplied.
        hours = None
        if "--hours" in sys.argv:
            hours = float(sys.argv[sys.argv.index("--hours") + 1])
        submit_task(desc, dept, estimated_hours=hours)

    elif command == "process":
        sequential = "--sequential" in sys.argv
        process_tasks(parallel=not sequential)

    elif command == "list":
        status = None
        if "--status" in sys.argv:
            status = sys.argv[sys.argv.index("--status") + 1]
        list_tasks(status)

    elif command == "show":
        if len(sys.argv) < 3:
            print("Usage: python main_v2.py show <task_id>")
            return
        show_task(sys.argv[2])

    elif command == "status":
        show_status()

    elif command == "report":
        show_report()

    elif command == "workflow":
        if len(sys.argv) < 3:
            print("Usage: python main_v2.py workflow <list|start|next|complete|approve|retry|status> ...")
            return
        sub = sys.argv[2]

        if sub == "list":
            workflow_list()
        elif sub == "start":
            if len(sys.argv) < 4:
                print("Usage: python main_v2.py workflow start <template_id> [key=value ...]")
                return
            workflow_start(sys.argv[3], sys.argv[4:])
        elif sub == "next":
            if len(sys.argv) < 4:
                print("Usage: python main_v2.py workflow next <instance_id>")
                return
            workflow_next(sys.argv[3])
        elif sub == "complete":
            if len(sys.argv) < 5:
                print("Usage: python main_v2.py workflow complete <instance_id> <step_id>")
                return
            workflow_complete(sys.argv[3], sys.argv[4])
        elif sub == "approve":
            if len(sys.argv) < 5:
                print("Usage: python main_v2.py workflow approve <instance_id> <step_id> [--reject]")
                return
            approved = "--reject" not in sys.argv
            workflow_approve(sys.argv[3], sys.argv[4], approved)
        elif sub == "retry":
            if len(sys.argv) < 4:
                print("Usage: python main_v2.py workflow retry <instance_id>")
                return
            workflow_retry(sys.argv[3])
        elif sub == "status":
            if len(sys.argv) < 4:
                print("Usage: python main_v2.py workflow status <instance_id>")
                return
            workflow_status(sys.argv[3])
        else:
            print(f"Unknown workflow subcommand: {sub}")

    elif command == "strategy":
        show_strategy(apply="--apply" in sys.argv)

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
