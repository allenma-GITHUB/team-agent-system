#!/usr/bin/env python3
"""
Test tools.py: the read-only tool layer that gives agents something to
actually call instead of prose to generate.

The important tests here are the negative ones. A model WILL hallucinate
tool names and malformed arguments, so ToolRegistry.execute() must return
a readable failure the loop can hand back rather than raising and killing
the run. And the read-only boundary is the entire safety property of this
phase, so it's enforced in code and pinned by a test, not left to reviewer
memory.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

from agent_state import AgentProfile, AgentRegistry
from budgets import BudgetManager, CapacityManager
from tools import MAX_LISTED_TASKS, Tool, ToolRegistry, build_readonly_registry


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _noop():
    return "ok"


def _isolated_registry(tasks_file: str = "data/test_tools_tasks.json") -> ToolRegistry:
    budgets = BudgetManager(data_file="data/test_tools_budgets.json")
    budgets.allocate("qa_tools_eng", 10000)
    budgets.request_expense("qa_tools_eng", 2500, category="labor")

    agents = AgentRegistry(data_file="data/test_tools_agents.json")
    agents.register(AgentProfile(
        agent_id="qa_tools_agent", name="QA Tools Agent", agent_type="ManagerAgent",
        department="qa_tools_eng", expertise_areas=["ops"], skill_level=3,
        capabilities=[], constraints=[], max_concurrent_tasks=4,
    ))
    agents.get("qa_tools_agent").current_workload = 1

    return build_readonly_registry(
        budgets=budgets, capacity=CapacityManager(registry=agents), tasks_file=tasks_file,
    )


def test_registry_refuses_to_register_a_mutating_tool():
    """The read-only boundary is the safety property of this phase."""
    print_section("1. Read-Only Registry Refuses Mutating Tools")

    registry = ToolRegistry(allow_mutating=False)
    mutating = Tool(name="delete_everything", description="x",
                    parameters={"type": "object", "properties": {}},
                    handler=_noop, mutates=True)

    try:
        registry.register(mutating)
        raise AssertionError("expected ValueError for a mutating tool")
    except ValueError as exc:
        print(f"  refused: {str(exc)[:80]}...")

    assert "delete_everything" not in registry.names()

    permissive = ToolRegistry(allow_mutating=True)
    permissive.register(mutating)
    assert "delete_everything" in permissive.names()
    print("  an explicitly permissive registry still accepts it")


def test_unknown_tool_returns_a_result_instead_of_raising():
    """A hallucinated tool name is ordinary control flow, not a crash."""
    print_section("2. Unknown Tool Returns A Readable Failure")

    registry = _isolated_registry()
    result = registry.execute("rm_rf_slash", {})
    print(f"  ok={result.ok} error={result.error}")
    print(f"  content={result.content[:70]}...")

    assert result.ok is False
    assert result.error == "unknown_tool"
    # The message must name the real tools, so the model can self-correct.
    assert "get_department_budget" in result.content


def test_bad_arguments_return_readable_failures():
    print_section("3. Missing / Unexpected / Non-Object Arguments")

    registry = _isolated_registry()

    missing = registry.execute("get_department_budget", {})
    print(f"  missing:    {missing.error} | {missing.content}")
    assert missing.ok is False and missing.error == "missing_arguments"

    unexpected = registry.execute("list_departments", {"surprise": 1})
    print(f"  unexpected: {unexpected.error} | {unexpected.content}")
    assert unexpected.ok is False and unexpected.error == "unexpected_arguments"

    not_an_object = registry.execute("list_departments", "engineering")
    print(f"  non-object: {not_an_object.error} | {not_an_object.content}")
    assert not_an_object.ok is False and not_an_object.error == "bad_arguments"


def test_handler_exception_becomes_a_result_not_a_crash():
    """One broken tool must not take down the whole agent run."""
    print_section("4. A Throwing Handler Is Reported, Not Propagated")

    def explode():
        raise RuntimeError("disk on fire")

    registry = ToolRegistry()
    registry.register(Tool(name="explode", description="x",
                            parameters={"type": "object", "properties": {}}, handler=explode))

    result = registry.execute("explode", {})
    print(f"  ok={result.ok} error={result.error} content={result.content}")

    assert result.ok is False
    assert result.error == "handler_error"
    assert "disk on fire" in result.content


def test_readonly_tools_return_real_injected_state():
    print_section("5. Tools Read Real State From Injected Managers")

    registry = _isolated_registry()

    budget = json.loads(registry.execute("get_department_budget", {"department": "qa_tools_eng"}).content)
    print(f"  budget: {budget}")
    assert budget["allocated"] == 10000
    assert budget["spent"] == 2500
    assert budget["utilization_pct"] == 25.0

    capacity = json.loads(registry.execute("get_department_capacity", {"department": "qa_tools_eng"}).content)
    print(f"  capacity: {capacity}")
    assert capacity["agent_count"] == 1
    assert capacity["total_capacity"] == 4
    assert capacity["current_workload"] == 1
    assert capacity["utilization_pct"] == 25.0

    unknown = json.loads(registry.execute("get_department_budget", {"department": "nope"}).content)
    print(f"  unallocated department: {unknown}")
    assert unknown["allocated"] is None  # reported honestly, not defaulted to 0


def test_list_tasks_caps_output_and_says_so():
    """An uncapped list would blow out the prompt and let the model reason
    over a truncated list it believes is complete."""
    print_section("6. list_tasks Caps Results And Discloses The Cap")

    tasks_file = "data/test_tools_many_tasks.json"
    Path(tasks_file).parent.mkdir(parents=True, exist_ok=True)
    Path(tasks_file).write_text(json.dumps([
        {"id": str(i), "description": f"task {i}", "department": "engineering",
         "status": "queued" if i % 2 else "completed", "estimated_hours": 1.0}
        for i in range(MAX_LISTED_TASKS * 2)
    ]))

    registry = _isolated_registry(tasks_file=tasks_file)
    payload = json.loads(registry.execute("list_tasks", {}).content)
    print(f"  total_matching={payload['total_matching']} showing={payload['showing']}")
    print(f"  note={payload.get('note')}")

    assert payload["total_matching"] == MAX_LISTED_TASKS * 2
    assert payload["showing"] == MAX_LISTED_TASKS
    assert "note" in payload  # the truncation is disclosed, not silent

    filtered = json.loads(registry.execute("list_tasks", {"status": "queued"}).content)
    print(f"  filtered to queued: total_matching={filtered['total_matching']}")
    assert filtered["total_matching"] == MAX_LISTED_TASKS
    assert all(t["status"] == "queued" for t in filtered["tasks"])

    missing = json.loads(_isolated_registry(tasks_file="data/test_tools_absent.json")
                          .execute("list_tasks", {}).content)
    print(f"  absent task file: {missing}")
    assert missing["tasks"] == []


def test_no_filesystem_tool_is_exposed():
    """Pins the security decision: "read-only" is not "safe". A generic
    file reader would let an agent pull .env into a prompt that then gets
    persisted verbatim into tasks.json."""
    print_section("7. No Generic Filesystem Tool Is Exposed")

    names = _isolated_registry().names()
    print(f"  tools: {names}")

    for forbidden in ("read_file", "open_file", "cat", "run_shell", "exec"):
        assert forbidden not in names

    schemas = _isolated_registry().schemas()
    assert [s["name"] for s in schemas] == sorted(s["name"] for s in schemas)  # stable prompt prefix
    assert all({"name", "description", "input_schema"} == set(s) for s in schemas)
    print("  schemas are sorted and well-formed")


if __name__ == "__main__":
    for _ in range(2):  # run twice back-to-back: no hidden cross-run state
        test_registry_refuses_to_register_a_mutating_tool()
        test_unknown_tool_returns_a_result_instead_of_raising()
        test_bad_arguments_return_readable_failures()
        test_handler_exception_becomes_a_result_not_a_crash()
        test_readonly_tools_return_real_injected_state()
        test_list_tasks_caps_output_and_says_so()
        test_no_filesystem_tool_is_exposed()

    print("\n✅ All tools tests passed!\n")
