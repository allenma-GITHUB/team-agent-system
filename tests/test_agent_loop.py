#!/usr/bin/env python3
"""
Test agent_loop.py: the multi-turn cycle that turns a completion into an
agent that can look things up.

The load-bearing test here is test_max_iterations_is_never_reported_as_completed.
This codebase has shipped the "unfinished work reported as finished" bug
three separate times (escalated tasks recorded as completed; workflow steps
marked done when execution escalated; escalations stored with a cheerful
summary). A loop that returned its last half-formed sentence as a finished
answer would be that same defect in a new place, so it is pinned here.

Uses scripted fake providers rather than a real API: this container has no
API key, and a test whose assertions depend on what a model felt like
doing that day isn't a regression test.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_loop import run_agent_loop
from tools import Tool, ToolRegistry


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


class ScriptedProvider:
    """Replays a fixed list of provider turns, then repeats the last one."""

    def __init__(self, turns):
        self.turns = turns
        self.seen_messages = []

    def generate_with_tools(self, messages, tool_schemas, task_type="general"):
        self.seen_messages.append(list(messages))
        index = min(len(self.seen_messages) - 1, len(self.turns) - 1)
        return self.turns[index]


def _turn(content="", tool_calls=None, tokens=10):
    return {
        "content": content,
        "tool_calls": tool_calls or [],
        "provider": "scripted",
        "model": "ScriptedModel",
        "tokens_estimate": tokens,
    }


def _registry(calls_log=None):
    registry = ToolRegistry()

    def lookup(department: str):
        if calls_log is not None:
            calls_log.append(department)
        return {"department": department, "allocated": 1000}

    registry.register(Tool(
        name="get_budget", description="Look up a budget.",
        parameters={"type": "object",
                    "properties": {"department": {"type": "string"}},
                    "required": ["department"]},
        handler=lookup,
    ))
    return registry


def test_loop_executes_tools_then_answers():
    print_section("1. Loop Calls A Tool, Feeds The Result Back, Then Answers")

    executed = []
    provider = ScriptedProvider([
        _turn(tool_calls=[{"id": "c1", "name": "get_budget", "arguments": {"department": "engineering"}}]),
        _turn(content="Engineering has $1000."),
    ])

    result = run_agent_loop("How much does engineering have?", provider, _registry(executed))

    print(f"  stopped_reason={result.stopped_reason} iterations={result.iterations}")
    print(f"  tool calls: {[(c.name, c.ok) for c in result.tool_calls]}")
    print(f"  final: {result.final_content}")

    assert result.completed() is True
    assert result.iterations == 2
    assert executed == ["engineering"]  # the tool really ran, with the model's argument
    assert result.final_content == "Engineering has $1000."
    assert result.successful_tool_calls() == 1

    # The tool's real output must reach the model on the next turn.
    second_turn_messages = provider.seen_messages[1]
    tool_messages = [m for m in second_turn_messages if m["role"] == "tool"]
    print(f"  tool result fed back: {tool_messages[0]['content'][:60]}")
    assert len(tool_messages) == 1
    assert "1000" in tool_messages[0]["content"]
    assert tool_messages[0]["tool_call_id"] == "c1"


def test_max_iterations_is_never_reported_as_completed():
    """The load-bearing honesty test - see this module's docstring."""
    print_section("2. Hitting The Iteration Cap Is Reported As Incomplete")

    # A model that never stops asking for tools.
    forever = ScriptedProvider([
        _turn(content="still working",
              tool_calls=[{"id": "c", "name": "get_budget", "arguments": {"department": "engineering"}}]),
    ])

    result = run_agent_loop("go forever", forever, _registry(), max_iterations=3)

    print(f"  stopped_reason={result.stopped_reason}")
    print(f"  completed()={result.completed()}")
    print(f"  iterations={result.iterations} tool_calls={len(result.tool_calls)}")

    assert result.stopped_reason == "max_iterations"
    assert result.completed() is False
    assert result.iterations == 3
    assert len(result.tool_calls) == 3  # bounded: exactly one call per iteration
    # It may still carry partial prose - but that must not read as a conclusion.
    assert result.final_content == "still working"


def test_failed_tool_result_is_fed_back_not_raised():
    """A hallucinated tool name must reach the model as text it can correct."""
    print_section("3. A Failed Tool Call Is Recorded And Returned To The Model")

    provider = ScriptedProvider([
        _turn(tool_calls=[{"id": "c1", "name": "no_such_tool", "arguments": {}}]),
        _turn(content="Sorry, I used the wrong tool."),
    ])

    result = run_agent_loop("do something", provider, _registry())

    print(f"  tool calls: {[(c.name, c.ok, c.error) for c in result.tool_calls]}")
    assert result.completed() is True
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].ok is False
    assert result.tool_calls[0].error == "unknown_tool"
    assert result.successful_tool_calls() == 0  # failures are not counted as successes

    fed_back = [m for m in provider.seen_messages[1] if m["role"] == "tool"][0]
    print(f"  fed back to model: {fed_back['content'][:60]}...")
    assert "No such tool" in fed_back["content"]


def test_bad_arguments_from_the_model_do_not_crash_the_run():
    print_section("4. Malformed Model Arguments Are Survivable")

    provider = ScriptedProvider([
        _turn(tool_calls=[{"id": "c1", "name": "get_budget", "arguments": {}}]),  # missing 'department'
        _turn(content="recovered"),
    ])

    result = run_agent_loop("ask badly", provider, _registry())

    print(f"  {[(c.name, c.ok, c.error) for c in result.tool_calls]}")
    assert result.completed() is True
    assert result.tool_calls[0].error == "missing_arguments"


def test_answer_without_tools_completes_in_one_iteration():
    print_section("5. An Answer Needing No Tools Costs One Iteration")

    provider = ScriptedProvider([_turn(content="No lookup needed.")])
    result = run_agent_loop("hello", provider, _registry())

    print(f"  iterations={result.iterations} tool_calls={len(result.tool_calls)}")
    assert result.completed() is True
    assert result.iterations == 1
    assert result.tool_calls == []


def test_tokens_accumulate_across_iterations():
    """Token spend is the real cost signal this system has been missing -
    it must be summed across the whole loop, not just the final turn."""
    print_section("6. Token Estimates Accumulate Across The Whole Loop")

    provider = ScriptedProvider([
        _turn(tool_calls=[{"id": "c1", "name": "get_budget", "arguments": {"department": "design"}}], tokens=30),
        _turn(content="done", tokens=12),
    ])

    result = run_agent_loop("q", provider, _registry())
    print(f"  tokens_estimate={result.tokens_estimate} (expected 42)")
    assert result.tokens_estimate == 42
    assert result.provider == "scripted"
    assert result.model == "ScriptedModel"


def test_parallel_tool_calls_in_one_turn_all_execute():
    print_section("7. Multiple Tool Calls In One Turn All Execute")

    executed = []
    provider = ScriptedProvider([
        _turn(tool_calls=[
            {"id": "c1", "name": "get_budget", "arguments": {"department": "engineering"}},
            {"id": "c2", "name": "get_budget", "arguments": {"department": "design"}},
        ]),
        _turn(content="both looked up"),
    ])

    result = run_agent_loop("compare two departments", provider, _registry(executed))

    print(f"  executed={executed}")
    assert executed == ["engineering", "design"]
    assert len(result.tool_calls) == 2
    assert result.successful_tool_calls() == 2

    tool_messages = [m for m in provider.seen_messages[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]


def test_invalid_max_iterations_is_rejected():
    print_section("8. max_iterations Below 1 Is Rejected")

    try:
        run_agent_loop("q", ScriptedProvider([_turn(content="x")]), _registry(), max_iterations=0)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        print(f"  ValueError: {exc}")


if __name__ == "__main__":
    for _ in range(2):  # run twice back-to-back: the loop holds no global state
        test_loop_executes_tools_then_answers()
        test_max_iterations_is_never_reported_as_completed()
        test_failed_tool_result_is_fed_back_not_raised()
        test_bad_arguments_from_the_model_do_not_crash_the_run()
        test_answer_without_tools_completes_in_one_iteration()
        test_tokens_accumulate_across_iterations()
        test_parallel_tool_calls_in_one_turn_all_execute()
        test_invalid_max_iterations_is_rejected()

    print("\n✅ All agent loop tests passed!\n")
