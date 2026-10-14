#!/usr/bin/env python3
"""
A failed tool call was counted but never named.

agent_loop.py's own docstring already names the exact value of this signal:
"the model asked for a tool that doesn't exist three times is exactly the
signal that tells you a tool's description is wrong, and it's invisible if
only successes are kept." That invisibility was real: run_agent_loop()
builds a full ToolCallRecord (name, arguments, ok, error) for every call,
but DepartmentHeadAgent._run_locked() reduced the whole list down to two
bare counts (tool_calls, tool_calls_failed) before it ever left the method -
confirmed live, pre-fix, with a hallucinated tool name: tool_calls_failed=1,
and no field anywhere in the result said which tool or why.

This pins the fix: `tool_call_errors` (a list of {"tool", "error"}) now
rides alongside the counts, present (possibly empty) whenever used_tools is
True, absent when a task never reached execution at all - the same
absence-means-never-ran convention tool_calls/used_tools themselves already
follow (checkpoint 46/47).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
from budgets import BudgetManager, CapacityManager
from performance import PerformanceAnalytics
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


class ScriptedToolProvider:
    """Requests one named tool, then answers - same shape as
    test_executor_tool_wiring.py's own double, duplicated here so this file
    stays runnable on its own (project convention: no cross-test imports)."""

    def __init__(self, tool_name="get_department_budget", arguments=None):
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {"department": "engineering"}
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        if self.turns == 1:
            return {
                "content": "", "tool_calls": [
                    {"id": "c1", "name": self.tool_name, "arguments": self.arguments}],
                "provider": "fake", "model": "FakeModel", "tokens_estimate": 20,
            }
        return {
            "content": "Plan: proceed within budget.", "tool_calls": [],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 5,
        }


class AlwaysWantsToolsProvider:
    """Never stops asking for tools - the iteration-exhaustion case, with a
    bad tool name mixed in so a run that never finishes can still be asked
    "which tool call(s) actually failed along the way?"."""

    def __init__(self, tool_name="not_a_real_tool"):
        self.tool_name = tool_name
        self.turns = 0

    def supports_tools(self, task_type="general"):
        return True

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.turns += 1
        return {
            "content": "still thinking",
            "tool_calls": [{"id": f"c{self.turns}", "name": self.tool_name, "arguments": {}}],
            "provider": "fake", "model": "FakeModel", "tokens_estimate": 7,
        }


def _agent(provider, suffix, department="engineering"):
    registry = AgentRegistry(data_file=f"data/test_tool_errors_agents_{suffix}.json")
    state = registry.register(AgentProfile(
        agent_id=f"{department}_head", name=f"{department.title()} Head",
        agent_type="ManagerAgent", department=department,
        expertise_areas=[department], skill_level=4,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    ))
    state.current_workload = 0
    state.learned_preferences = {}
    # register() returns an existing state if a prior run left one on disk;
    # metrics accumulate, so reset them to keep absolute assertions stable.
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_tool_errors_budgets_{suffix}.json")
    budgets.allocate(department, 100000)

    agent = DepartmentHeadAgent(
        department, llm_provider=provider,
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_tool_errors_metrics_{suffix}.json"),
    )
    return agent, registry, budgets


def test_hallucinated_tool_name_is_named_and_explained():
    print_section("1. A Failed Tool Call Now Says Which Tool And Why")

    provider = ScriptedToolProvider(tool_name="get_quarterly_forecast", arguments={})
    agent, _, _ = _agent(provider, "a")

    result = agent.run("Plan the Q3 roadmap", 1.0)

    print(f"  status            : {result['status']}")
    print(f"  tool_calls_failed : {result['tool_calls_failed']}")
    print(f"  tool_call_errors  : {result['tool_call_errors']}")

    assert result["status"] == "completed"
    assert result["tool_calls_failed"] == 1
    assert result["tool_call_errors"] == [
        {"tool": "get_quarterly_forecast", "error": "unknown_tool"}
    ], "the failing tool's name and error code must both be named, not just counted"


def test_all_successful_tool_calls_yields_an_empty_but_present_list():
    print_section("2. No Failures -> Present, Empty List (Not Absent)")

    provider = ScriptedToolProvider()  # real tool, real argument - succeeds
    agent, _, _ = _agent(provider, "b")

    result = agent.run("Check the budget before planning", 1.0)

    print(f"  tool_calls_failed : {result['tool_calls_failed']}")
    print(f"  tool_call_errors  : {result['tool_call_errors']}")

    assert result["tool_calls_failed"] == 0
    assert "tool_call_errors" in result, "used_tools=True must always carry this key, like tool_calls_failed does"
    assert result["tool_call_errors"] == []


def test_escalated_before_execution_has_no_tool_call_errors_key():
    """A task that never reaches the LLM at all (decision-engine escalation,
    budget denial) must not carry a placeholder tool_call_errors - absence
    here means "never ran," the same contract used_tools/tool_calls already
    follow (checkpoint 46/47's own convention, confirmed unbroken)."""
    print_section("3. An Escalated-Before-Execution Task Has No Tool Fields At All")

    registry = AgentRegistry(data_file="data/test_tool_errors_agents_c.json")
    state = registry.register(AgentProfile(
        agent_id="solo_head", name="Solo Head", agent_type="ManagerAgent",
        department="solo", expertise_areas=["solo"], skill_level=4,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=1,
    ))
    state.current_workload = 0
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file="data/test_tool_errors_budgets_c.json")
    budgets.allocate("solo", 0)  # no budget at all -> request_expense must deny

    agent = DepartmentHeadAgent(
        "solo", llm_provider=ScriptedToolProvider(),
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file="data/test_tool_errors_metrics_c.json"),
    )

    result = agent.run("Task with no budget to cover it", 1.0)
    print(f"  status     : {result['status']}")
    print(f"  result keys: {sorted(result.keys())}")

    assert result["status"] == "escalated"
    for absent in ("used_tools", "tool_calls", "tool_calls_failed", "tool_call_errors"):
        assert absent not in result, f"{absent} should never have been computed for this task"


def test_max_iterations_exhaustion_still_names_any_failures_along_the_way():
    """The run never finishes (escalated, not completed) but it DID spend
    iterations on a tool that doesn't exist the whole time - that failure is
    real and already recorded even though the task itself didn't."""
    print_section("4. Iteration-Exhaustion Escalation Still Carries Its Tool Errors")

    provider = AlwaysWantsToolsProvider(tool_name="not_a_real_tool")
    agent, registry, _ = _agent(provider, "d")

    result = agent.run("Task the agent never finishes", 1.0)

    print(f"  status           : {result['status']}")
    print(f"  tool_call_errors : {result['tool_call_errors']}")

    assert result["status"] == "escalated"
    assert result["approved"] is False
    assert result["tool_calls_failed"] == len(result["tool_call_errors"])
    assert all(e == {"tool": "not_a_real_tool", "error": "unknown_tool"} for e in result["tool_call_errors"])
    assert len(result["tool_call_errors"]) >= 1


def test_cli_and_dashboard_render_the_new_field():
    """Pins the two consumers added alongside the computation: main_v2's CLI
    prints each failing tool/error line, and dashboard.html's detail view
    does the same - escaped, like every other dynamic field it renders
    (test_web_server.py's safety test covers escaping regressions on the
    existing fields; this one is new and must follow the same rule)."""
    print_section("5. Both The CLI And The Dashboard Render tool_call_errors")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cli_source = open(os.path.join(repo_root, "main_v2.py")).read()
    assert "tool_call_errors" in cli_source
    assert "err['tool']" in cli_source and "err['error']" in cli_source

    dashboard_source = open(os.path.join(repo_root, "static", "dashboard.html")).read()
    assert "result.tool_call_errors" in dashboard_source
    assert "escapeHtml(e.tool)" in dashboard_source
    assert "escapeHtml(e.error)" in dashboard_source, \
        "tool name/error must be escaped like every other dynamic field - an agent's hallucinated " \
        "tool name is model-generated text reaching innerHTML, not a trusted constant"


if __name__ == "__main__":
    for _ in range(2):  # idempotent: state is reset explicitly in _agent()
        test_hallucinated_tool_name_is_named_and_explained()
        test_all_successful_tool_calls_yields_an_empty_but_present_list()
        test_escalated_before_execution_has_no_tool_call_errors_key()
        test_max_iterations_exhaustion_still_names_any_failures_along_the_way()
        test_cli_and_dashboard_render_the_new_field()

    print("\n✅ All tool-call-error surfacing tests passed!\n")
