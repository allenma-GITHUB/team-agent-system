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

DATA_DIR = Path("data")
TASKS_FILE = DATA_DIR / "tasks.json"
RESULTS_DIR = DATA_DIR / "results"


def init_system():
    """Initialize directories and files."""
    DATA_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)

    if not TASKS_FILE.exists():
        TASKS_FILE.write_text(json.dumps([], indent=2))


def submit_task(description: str, department=None):
    """Submit a new task."""
    init_system()

    tasks = json.loads(TASKS_FILE.read_text())
    task_id = str(len(tasks) + 1).zfill(4)

    if not department:
        department = DepartmentManager.route_task(description)

    task = {
        "id": task_id,
        "description": description,
        "department": department,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "result": None
    }

    tasks.append(task)
    TASKS_FILE.write_text(json.dumps(tasks, indent=2))

    print(f"✓ Task {task_id} submitted to {department.upper()}")
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
        task_list = [(t["department"], t["description"]) for t in queued]
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
            result = executor.execute(task["department"], task["description"])
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


def main():
    if len(sys.argv) < 2:
        print("Team Agent System v2 - CLI with Event Bus & Parallel Execution")
        print("\nUsage:")
        print("  python main_v2.py submit <description> [--dept DEPARTMENT]")
        print("  python main_v2.py process [--sequential]")
        print("  python main_v2.py list [--status STATUS]")
        print("  python main_v2.py show <task_id>")
        print("  python main_v2.py status")
        print("\nExample:")
        print("  python main_v2.py submit 'Fix login bug'")
        print("  python main_v2.py process          # Parallel by default")
        print("  python main_v2.py process --sequential  # Force sequential")
        return

    command = sys.argv[1]

    if command == "submit":
        if len(sys.argv) < 3:
            print("Usage: python main_v2.py submit <description> [--dept DEPARTMENT]")
            return
        desc = sys.argv[2]
        dept = None
        if "--dept" in sys.argv:
            dept = sys.argv[sys.argv.index("--dept") + 1]
        submit_task(desc, dept)

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

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
