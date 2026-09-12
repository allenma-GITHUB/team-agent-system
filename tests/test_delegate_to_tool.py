#!/usr/bin/env python3
"""
Test delegate_to: the tool that lets an agent decide to hand off a task
mid-reasoning, instead of delegation only ever being inferred upstream by
the decision engine's can_execute()/should_execute() checks
(agent_decisions.py) before any tool loop runs.

Two defects are covered alongside the new happy path, both found while
designing this rather than assumed away:

1. A DELEGATE_TO CALL INSIDE THE LOOP WOULD DEADLOCK IF EXECUTED INLINE.
   The tool loop runs inside DepartmentHeadAgent._run_locked(), i.e. while
   this agent's own lock is held. A handler that called another agent's
   run() directly from inside the tool loop would re-enter checkpoint 35's
   exact lock-ordering hazard. delegate_to's handler never calls another
   agent - it only returns a confirmation payload; _run_locked() converts a
   successful call into the same _DelegationRequest the decision engine
   already produces, and task_executor_v2.run() dispatches it only after
   the lock is released, same as every other delegation path.

2. _resolve_delegate() COULD NOT FIND A DEPARTMENT THAT HAD NEVER RUN A
   TASK YET IN THIS PROCESS. Confirmed live before this fix:
   `TaskExecutor()._resolve_delegate("engineering_head")` returned None on
   a fresh registry with zero prior engineering tasks, even though
   engineering is a real, fully runnable department - get_agent_for_department()
   constructs a department head on demand and does not need an AgentState
   to already exist. Without the fix, delegate_to would appear to work but
   silently degrade to "no channel, execute here instead" for the single
   most common case: the first-ever delegation to a given department.

The deadlock-shaped tests run in a worker thread with a join timeout, per
this repo's convention for scenarios that would hang rather than fail an
assertion.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent, TaskExecutor
from tools import build_delegation_tool

DEADLOCK_TIMEOUT_SECONDS = 20


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _run_with_deadlock_timeout(fn):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout=DEADLOCK_TIMEOUT_SECONDS)

    assert not worker.is_alive(), (
        f"DEADLOCK: run did not return within {DEADLOCK_TIMEOUT_SECONDS}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


class DelegatesToProvider:
    """Calls delegate_to once, targeting `department`, then would answer
    normally if the loop kept going - it must not get the chance to."""

    def __init__(self, department, reason="the other team owns this"):
        self.department = department
        self.reason = reason
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        if self.turns == 1:
            return {
                "content": "", "tool_calls": [
                    {"id": "c1", "name": "delegate_to",
                     "arguments": {"department": self.department, "reason": self.reason}}],
                "provider": "fake", "model": "FakeModel", "tokens_estimate": 12,
            }
        # Should never be reached for a successful delegation: the loop
        # would call this again only if _run_locked() failed to stop after
        # a successful delegate_to call.
        return {
            "content": "I did the work myself after all.", "tool_calls": [],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 5,
        }


class ScriptedProvider:
    """Answers immediately, no tools. Stands in for a delegate that just
    does the work once it receives it."""

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        return {
            "content": "Handled.", "tool_calls": [],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 5,
        }


def _profile(agent_id, department, skill=4):
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=[department], skill_level=skill,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    )


def _executor(departments, providers, suffix):
    """Build a TaskExecutor with real cross-department delegation wiring
    (delegate_resolver), one isolated set of data files per test.

    `providers` maps department -> a single fake provider INSTANCE (not a
    factory) reused across every call the loop makes for that department -
    ScriptedProvider/DelegatesToProvider track state (how many turns have
    happened) across the calls within one run, so a fresh instance per call
    would silently reset that state every turn.
    """
    registry = AgentRegistry(data_file=f"data/test_deltool_agents_{suffix}.json")
    for department in departments:
        state = registry.register(_profile(f"{department}_head", department))
        state.current_workload = 0
        state.learned_preferences = {}
        state.metrics = PerformanceMetrics()  # reset: metrics accumulate across runs

    budgets = BudgetManager(data_file=f"data/test_deltool_budgets_{suffix}.json")
    for department in departments:
        budgets.allocate(department, 100000)

    class _Provider:
        """Routes to a different fake provider per department, so the
        delegator and the delegate can behave differently in one run."""

        def supports_tools(self, task_type="general"):
            return providers[task_type].supports_tools(task_type)

        def generate_with_tools(self, messages, tool_schemas, task_type="general"):
            return providers[task_type].generate_with_tools(messages, tool_schemas, task_type)

    executor = TaskExecutor(
        _Provider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_deltool_metrics_{suffix}.json"),
    )
    return executor, registry, budgets


def test_tool_rejects_self_delegation():
    from tools import ToolRegistry

    print_section("1. delegate_to Refuses To Target Your Own Department")

    tool = build_delegation_tool("engineering")
    registry = ToolRegistry(allow_mutating=False)
    registry.register(tool)

    result = registry.execute("delegate_to", {"department": "engineering", "reason": "x"})
    print(f"  ok={result.ok} content={result.content}")
    assert result.ok is False
    assert result.error == "handler_error"
    assert "own department" in result.content


def test_tool_rejects_unknown_department():
    from tools import ToolRegistry

    print_section("2. delegate_to Rejects A Hallucinated Department Name")

    tool = build_delegation_tool("engineering")
    registry = ToolRegistry(allow_mutating=False)
    registry.register(tool)

    result = registry.execute("delegate_to", {"department": "marketing", "reason": "x"})
    print(f"  ok={result.ok} content={result.content}")
    assert result.ok is False
    assert "Unknown department" in result.content
    assert "engineering" in result.content, "the error should list valid departments"


def test_tool_accepts_a_real_other_department():
    from tools import ToolRegistry

    print_section("3. delegate_to Accepts A Real, Different Department")

    tool = build_delegation_tool("support")
    registry = ToolRegistry(allow_mutating=False)
    registry.register(tool)

    result = registry.execute("delegate_to", {"department": "engineering", "reason": "needs architecture work"})
    print(f"  ok={result.ok} content={result.content}")
    assert result.ok is True
    assert "engineering" in result.content
    assert "not guaranteed" in result.content, "must not overstate what calling this actually does"


def test_agent_initiated_delegation_hands_off_to_a_never_before_run_department():
    """The core new capability, and the regression test for the
    _resolve_delegate fix: engineering has never run a single task in this
    executor before this call."""
    print_section("4. An Agent-Initiated delegate_to Call Actually Delegates")

    executor, registry, budgets = _executor(
        ["support", "engineering"],
        providers={"support": DelegatesToProvider("engineering"), "engineering": ScriptedProvider()},
        suffix="a",
    )

    result = _run_with_deadlock_timeout(
        lambda: executor.get_agent_for_department("support").run(
            "Design a new architecture for the platform", 3.0))

    print(f"  status          : {result.get('status')}")
    print(f"  executed by     : {result.get('agent_id')}")
    print(f"  delegated_from  : {result.get('delegated_from')}")
    print(f"  delegation_chain: {result.get('delegation_chain')}")
    print(f"  reasoning       : {result.get('delegation_reasoning')}")

    assert result["status"] == "completed"
    assert result["agent_id"] == "engineering_head", "the delegate must be the one that ran"
    assert result["delegated_from"] == "support_head"
    assert result["delegation_chain"] == ["support_head"]
    assert "delegate_to tool" in result["delegation_reasoning"]
    assert result["analysis"] == "Handled."

    # Both departments paid: support spent time reasoning before deciding to
    # hand off (see the comment in _run_locked - that budget is not
    # refunded), and engineering was billed for actually doing the work.
    assert budgets.get_budget("support").spent > 0
    assert budgets.get_budget("engineering").spent > 0

    # Support is not credited with a task it didn't do; workload is released
    # rather than left permanently occupied.
    support_state = registry.get("support_head")
    assert support_state.metrics.tasks_completed == 0
    assert support_state.current_workload == 0
    assert registry.get("engineering_head").metrics.tasks_completed == 1


def test_delegation_cycle_via_tool_escalates_instead_of_deadlocking():
    """engineering -> design -> engineering, both hops driven by the tool
    rather than the decision engine. delegate_to only accepts real,
    configured department names (design.get_departments()), so this uses
    two of the five built-in ones rather than arbitrary placeholders. Naive
    inline execution of the tool's target would deadlock; this proves the
    signal-then-dispatch-outside-the-lock design holds for the tool-driven
    path too, not just the original decision-engine one."""
    print_section("5. A Delegation Cycle Through delegate_to Escalates, Not Hangs")

    executor, _, budgets = _executor(
        ["engineering", "design"],
        providers={"engineering": DelegatesToProvider("design"),
                   "design": DelegatesToProvider("engineering")},
        suffix="b",
    )

    result = _run_with_deadlock_timeout(
        lambda: executor.get_agent_for_department("engineering").run("Hot potato", 1.0))

    print(f"  status  : {result.get('status')}")
    print(f"  analysis: {result.get('analysis')}")

    assert result["status"] == "escalated"
    assert "cycle" in result["analysis"].lower()


def test_forced_local_execution_ignores_a_repeated_delegate_to_call():
    """_dispatch_delegation's execute_here() retries with allow_delegation
    False specifically so a second delegation attempt cannot happen - this
    proves a delegate_to call is ignored (not honored, not an error) on
    that retry, so the agent actually does the work instead of bouncing."""
    print_section("6. allow_delegation=False Ignores A delegate_to Call")

    registry = AgentRegistry(data_file="data/test_deltool_agents_c.json")
    state = registry.register(_profile("engineering_head", "engineering"))
    state.current_workload = 0
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_deltool_budgets_c.json")
    budgets.allocate("engineering", 100000)

    agent = DepartmentHeadAgent(
        "engineering", llm_provider=DelegatesToProvider("design"),
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_deltool_metrics_c.json"),
    )  # no delegate_resolver: standalone, same as a no-channel scenario

    result = _run_with_deadlock_timeout(lambda: agent.run("Do it anyway", 1.0))

    print(f"  status             : {result.get('status')}")
    print(f"  delegation_declined: {result.get('delegation_declined')}")

    # No resolver at all means the FIRST attempt already declines and
    # forces local execution (allow_delegation=False on retry); the
    # delegate_to call the provider makes on that retry must be ignored.
    assert result["status"] == "completed"
    assert result["agent_id"] == "engineering_head"
    assert "no delegation channel" in result["delegation_declined"]


if __name__ == "__main__":
    for _ in range(2):  # run twice back-to-back: state is reset explicitly above
        test_tool_rejects_self_delegation()
        test_tool_rejects_unknown_department()
        test_tool_accepts_a_real_other_department()
        test_agent_initiated_delegation_hands_off_to_a_never_before_run_department()
        test_delegation_cycle_via_tool_escalates_instead_of_deadlocking()
        test_forced_local_execution_ignores_a_repeated_delegate_to_call()

    print("\n✅ All delegate_to tool tests passed!\n")
