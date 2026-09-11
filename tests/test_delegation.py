#!/usr/bin/env python3
"""
Test that a "delegate" decision actually delegates - and that doing so
cannot deadlock.

Two separate defects are covered here.

1. DELEGATION NEVER EXECUTED. agent_decisions.py has returned
   decision="delegate" with an assigned_agent_id since Phase 1, but
   task_executor_v2.py only ever branched on "escalate". A delegated task
   fell through to the budget charge and was executed by the ORIGINAL
   agent, which was also billed and credited for it, while the event
   trace recorded a hand-off that never happened.

2. WIRING IT UP NAIVELY WOULD DEADLOCK. find_best_delegate() ranked
   available_agents() without excluding the caller, so an agent could
   delegate to itself; DepartmentHeadAgent._lock is a plain non-reentrant
   Lock and TaskExecutor caches exactly one agent per department, so
   self-delegation re-enters a held lock, and an A->B->A cycle is a
   lock-ordering deadlock. Both would hang a worker thread forever rather
   than raising anything debuggable.

The deadlock tests therefore run in a worker thread with a join timeout:
a regression here does not fail an assertion, it hangs, so the timeout IS
the assertion.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading

from agent_decisions import AgentDecisionEngine, DecisionContext
from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from llm_provider import LLMProvider
from performance import PerformanceAnalytics
from task_executor_v2 import MAX_DELEGATION_DEPTH, DepartmentHeadAgent, TaskExecutor

DEADLOCK_TIMEOUT_SECONDS = 20


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _profile(agent_id: str, department: str, skill: int = 5) -> AgentProfile:
    # Every agent shares one expertise area so each is a legal delegate for
    # the others - find_best_delegate() filters candidates by the delegator's
    # first required skill.
    return AgentProfile(
        agent_id=agent_id, name=agent_id.replace("_", " ").title(),
        agent_type="ManagerAgent", department=department,
        expertise_areas=["shared"], skill_level=skill,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    )


def _make_executor(departments, disliked=(), suffix="a"):
    """Build an executor whose named departments all exist as agents.

    `disliked` departments get a strongly negative affinity for their own
    task type, which is what makes should_execute() return False and drives
    a delegate decision deterministically - no reliance on a model's mood.

    State is set explicitly after registration rather than assumed from a
    fresh file, so the test is idempotent no matter what previous runs left
    behind on disk.
    """
    registry = AgentRegistry(data_file=f"data/test_deleg_agents_{suffix}.json")
    for department in departments:
        agent_id = f"{department}_head"
        state = registry.register(_profile(agent_id, department))
        state.current_workload = 0
        state.learned_preferences = {department: -0.9} if department in disliked else {}
        # register() returns the EXISTING state if a previous run of this file
        # left one on disk, and metrics accumulate across runs - so reset them
        # rather than asserting on counts that drift every time the suite runs.
        state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_deleg_budgets_{suffix}.json")
    for department in departments:
        budgets.allocate(department, 100000)

    executor = TaskExecutor(
        LLMProvider(),
        agent_state_registry=registry,
        budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_deleg_metrics_{suffix}.json"),
    )
    return executor, registry, budgets


def _run_with_deadlock_timeout(fn):
    """Run fn() in a worker thread; fail if it does not finish in time.

    A deadlock regression hangs instead of raising, so an ordinary call
    would stall the whole suite forever with no diagnostic.
    """
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
        f"DEADLOCK: delegation did not return within {DEADLOCK_TIMEOUT_SECONDS}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def test_an_agent_never_delegates_to_itself():
    print_section("1. An Agent Is Excluded From Its Own Delegate Ranking")

    registry = AgentRegistry(data_file="data/test_deleg_agents_solo.json")
    state = registry.register(_profile("solo_head", "solo"))
    state.current_workload = 0

    engine = AgentDecisionEngine(state, registry=registry)
    context = DecisionContext(
        task_id="t1", task_type="solo", required_skills=["shared"],
        complexity=0.5, urgency=0.5, estimated_hours=1.0, required_approval_level=2,
    )

    delegate = engine.find_best_delegate(context)
    print(f"  only agent in registry is itself -> find_best_delegate() = {delegate}")
    assert delegate is None, "an agent must never be its own delegate"


def test_delegated_work_is_executed_and_billed_by_the_delegate():
    """The core defect: the delegator used to do the work and get billed."""
    print_section("2. The Delegate Does The Work, And Pays For It")

    executor, registry, budgets = _make_executor(
        ["engineering", "design"], disliked=["engineering"], suffix="b")

    result = _run_with_deadlock_timeout(
        lambda: executor.get_agent_for_department("engineering").run("Build a thing", 2.0))

    print(f"  status           : {result.get('status')}")
    print(f"  executed by      : {result.get('agent_id')}")
    print(f"  delegated_from   : {result.get('delegated_from')}")
    print(f"  delegation_chain : {result.get('delegation_chain')}")

    assert result["delegated_from"] == "engineering_head"
    assert result["agent_id"] == "design_head", "the delegate must be the one that ran"
    assert result["department"] == "design"
    assert result["delegation_chain"] == ["engineering_head"]
    assert result["status"] == "completed"

    eng_spent = budgets.get_budget("engineering").spent
    design_spent = budgets.get_budget("design").spent
    print(f"  engineering spent: ${eng_spent:,.2f} (must be 0 - it did not do the work)")
    print(f"  design spent     : ${design_spent:,.2f}")

    assert eng_spent == 0, "a delegating agent must not be billed for work it handed away"
    assert design_spent > 0, "the delegate must be billed for the work it did"

    # And the delegate, not the delegator, is credited with the task.
    assert registry.get("design_head").metrics.tasks_completed == 1
    assert registry.get("engineering_head").metrics.tasks_completed == 0


def test_delegation_cycle_escalates_instead_of_deadlocking():
    """A->B->A. Naive inline delegation would deadlock on the lock ordering;
    unbounded recursion would instead spin until the budget drained."""
    print_section("3. A Delegation Cycle Escalates, It Does Not Hang")

    executor, _, budgets = _make_executor(
        ["engineering", "design"], disliked=["engineering", "design"], suffix="c")

    result = _run_with_deadlock_timeout(
        lambda: executor.get_agent_for_department("engineering").run("Nobody wants this", 1.0))

    print(f"  status  : {result.get('status')}")
    print(f"  analysis: {result.get('analysis')}")

    assert result["status"] == "escalated"
    assert "cycle" in result["analysis"].lower()
    # Nothing was executed, so nothing was charged anywhere.
    assert budgets.get_budget("engineering").spent == 0
    assert budgets.get_budget("design").spent == 0


def test_delegation_depth_is_bounded():
    """Exercises the depth guard specifically, by entering with a chain that
    is already near the cap. Left to its own devices the ranking tends to
    bounce straight back to a previous agent and trip the *cycle* guard
    first, which would leave this branch untested.
    """
    print_section(f"4. Delegation Depth Is Capped At {MAX_DELEGATION_DEPTH}")

    departments = ["alpha", "beta", "gamma"]
    executor, _, budgets = _make_executor(departments, disliked=departments, suffix="d")

    # Two hops already happened upstream, and neither is a delegate candidate
    # here, so only the depth limit can stop this.
    prior_chain = ("upstream_one_head", "upstream_two_head")
    result = _run_with_deadlock_timeout(
        lambda: executor.get_agent_for_department("alpha").run(
            "Hot potato", 1.0, delegation_chain=prior_chain))

    print(f"  entered with chain: {list(prior_chain)}")
    print(f"  status  : {result.get('status')}")
    print(f"  analysis: {result.get('analysis')}")

    assert result["status"] == "escalated"
    assert "depth limit" in result["analysis"].lower(), result["analysis"]
    assert str(MAX_DELEGATION_DEPTH) in result["analysis"]
    # Escalated before doing anything, so no department paid for it.
    assert all(budgets.get_budget(d).spent == 0 for d in departments)

    # One hop shallower: alpha delegates, then the NEXT agent trips the cap.
    # The record that comes back must describe the deepest frame - the chain
    # that actually hit the limit - not alpha's shorter view of it, which is
    # what plain assignment during stack unwinding would leave behind.
    shallower = _run_with_deadlock_timeout(
        lambda: executor.get_agent_for_department("alpha").run(
            "Hot potato", 1.0, delegation_chain=("upstream_one_head",)))
    print(f"  one hop shallower -> status={shallower.get('status')} "
          f"chain={shallower.get('delegation_chain')}")
    print(f"                       reason={shallower.get('analysis')}")

    assert shallower["status"] == "escalated"
    chain = shallower["delegation_chain"]
    assert chain[0] == "upstream_one_head" and chain[1] == "alpha_head"
    assert len(chain) == MAX_DELEGATION_DEPTH, (
        f"the deepest frame's chain must survive stack unwinding, got {chain}")
    # The recorded chain and the stated reason must agree about how deep it got.
    assert " -> ".join(chain) in shallower["analysis"]


def test_standalone_agent_executes_and_flags_the_missed_delegation():
    """An agent built without a TaskExecutor has no way to reach a sibling.
    It does the work rather than failing a task that used to succeed - but
    records that the hand-off did not happen instead of staying silent."""
    print_section("5. No Delegation Channel: Execute Here, And Say So")

    registry = AgentRegistry(data_file="data/test_deleg_agents_e.json")
    for department in ("engineering", "design"):
        state = registry.register(_profile(f"{department}_head", department))
        state.current_workload = 0
        state.learned_preferences = {department: -0.9}

    budgets = BudgetManager(data_file="data/test_deleg_budgets_e.json")
    budgets.allocate("engineering", 100000)

    agent = DepartmentHeadAgent(
        "engineering", llm_provider=LLMProvider(),
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_deleg_metrics_e.json"),
    )  # note: no delegate_resolver

    result = _run_with_deadlock_timeout(lambda: agent.run("Do it anyway", 1.0))

    print(f"  status             : {result.get('status')}")
    print(f"  delegation_declined: {result.get('delegation_declined')}")
    print(f"  intended_delegate  : {result.get('intended_delegate')}")

    assert result["status"] == "completed"
    assert result["agent_id"] == "engineering_head"
    assert "no delegation channel" in result["delegation_declined"]
    assert result["intended_delegate"] == "design_head"
    # It really did the work it was flagged as doing.
    assert budgets.get_budget("engineering").spent > 0


def test_normal_execution_is_unaffected():
    """The overwhelmingly common path - an agent that wants its own task -
    must be untouched by any of this."""
    print_section("6. Ordinary Non-Delegated Execution Still Works")

    executor, registry, budgets = _make_executor(["engineering"], suffix="f")

    result = _run_with_deadlock_timeout(
        lambda: executor.execute("engineering", "Normal task", 1.0))

    print(f"  status={result.get('status')} summary={result.get('summary')}")
    assert result["status"] == "completed"
    assert budgets.get_budget("engineering").spent > 0
    assert registry.get("engineering_head").metrics.tasks_completed == 1


def test_concurrent_cross_department_delegation_does_not_deadlock():
    """The scenario that motivated dispatching outside the lock: two threads
    delegating in opposite directions at the same time."""
    print_section("7. Concurrent Opposing Delegations Do Not Deadlock")

    executor, _, _ = _make_executor(
        ["engineering", "design"], disliked=["engineering", "design"], suffix="g")

    def hammer():
        results = []
        with __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(
                max_workers=6) as pool:
            futures = [
                pool.submit(executor.get_agent_for_department(dept).run, f"task {i}", 1.0)
                for i in range(6)
                for dept in ("engineering", "design")
            ]
            for future in futures:
                results.append(future.result())
        return results

    results = _run_with_deadlock_timeout(hammer)
    print(f"  {len(results)} concurrent delegating runs all returned")
    assert len(results) == 12
    assert all(r.get("status") in ("completed", "escalated") for r in results)


if __name__ == "__main__":
    test_an_agent_never_delegates_to_itself()
    test_delegated_work_is_executed_and_billed_by_the_delegate()
    test_delegation_cycle_escalates_instead_of_deadlocking()
    test_delegation_depth_is_bounded()
    test_standalone_agent_executes_and_flags_the_missed_delegation()
    test_normal_execution_is_unaffected()
    test_concurrent_cross_department_delegation_does_not_deadlock()

    print("\n✅ All delegation tests passed!\n")
