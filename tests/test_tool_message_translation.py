#!/usr/bin/env python3
"""
Test the provider-neutral -> vendor message translation in llm_provider.py.

These are pure functions precisely so they can be tested here: this
container has no API keys, so the Anthropic and OpenAI tool paths can
never be exercised live, and an untested translation is where a
hand-rolled tool loop silently breaks in production.

Two rules are pinned below because both are enforced by the vendor APIs
and neither is obvious from reading a single-turn example:
  - Anthropic requires every tool_result answering one assistant turn to
    arrive in a SINGLE user message. Emitting one message per result is
    the classic way a loop breaks on its first *parallel* tool call - and
    it works fine right up until the model asks for two things at once.
  - Anthropic rejects empty text blocks, so an assistant turn that was
    pure tool_use must omit the text block rather than send "".
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from llm_provider import (
    LLMProvider,
    to_anthropic_messages,
    to_anthropic_tools,
    to_openai_messages,
    to_openai_tools,
    _safe_json_object,
)
from tools import build_readonly_registry


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


PARALLEL_CONVERSATION = [
    {"role": "user", "content": "compare engineering and design budgets"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "get_department_budget", "arguments": {"department": "engineering"}},
        {"id": "c2", "name": "get_department_budget", "arguments": {"department": "design"}},
    ]},
    {"role": "tool", "tool_call_id": "c1", "name": "get_department_budget", "content": '{"allocated": 50000}'},
    {"role": "tool", "tool_call_id": "c2", "name": "get_department_budget", "content": '{"allocated": 30000}'},
]


def test_anthropic_merges_parallel_tool_results_into_one_message():
    print_section("1. Anthropic: Parallel tool_results Merge Into One User Message")

    out = to_anthropic_messages(PARALLEL_CONVERSATION)
    print(f"  roles: {[m['role'] for m in out]}")

    result_messages = [
        m for m in out
        if isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result"
    ]
    assert len(result_messages) == 1, "both tool_results must share ONE user message"
    assert len(result_messages[0]["content"]) == 2
    assert [b["tool_use_id"] for b in result_messages[0]["content"]] == ["c1", "c2"]
    print("  both results carried in a single user message, ids preserved")


def test_anthropic_omits_empty_assistant_text_block():
    print_section("2. Anthropic: Empty Assistant Text Block Is Omitted")

    out = to_anthropic_messages(PARALLEL_CONVERSATION)
    assistant = [m for m in out if m["role"] == "assistant"][0]
    print(f"  block types: {[b['type'] for b in assistant['content']]}")

    assert all(block["type"] != "text" for block in assistant["content"])
    assert [b["name"] for b in assistant["content"]] == ["get_department_budget"] * 2
    assert assistant["content"][0]["input"] == {"department": "engineering"}


def test_anthropic_keeps_assistant_text_when_present():
    print_section("3. Anthropic: Real Assistant Text Is Preserved Before tool_use")

    out = to_anthropic_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Let me check.", "tool_calls": [
            {"id": "c1", "name": "list_departments", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "list_departments", "content": "{}"},
    ])
    assistant = [m for m in out if m["role"] == "assistant"][0]
    print(f"  blocks: {[(b['type'], b.get('text', '')) for b in assistant['content']]}")

    assert assistant["content"][0] == {"type": "text", "text": "Let me check."}
    assert assistant["content"][1]["type"] == "tool_use"


def test_openai_keeps_tool_results_as_separate_tool_role_messages():
    print_section("4. OpenAI: Results Stay Separate, Args Serialized As JSON Strings")

    out = to_openai_messages(PARALLEL_CONVERSATION)
    print(f"  roles: {[m['role'] for m in out]}")

    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool"]

    assistant = out[1]
    assert assistant["content"] is None  # OpenAI wants null, not ""
    raw_args = assistant["tool_calls"][0]["function"]["arguments"]
    print(f"  first call arguments (raw): {raw_args!r}")
    assert isinstance(raw_args, str)
    assert json.loads(raw_args) == {"department": "engineering"}
    assert assistant["tool_calls"][0]["type"] == "function"

    assert [m["tool_call_id"] for m in out[2:]] == ["c1", "c2"]


def test_tool_schemas_translate_to_both_vendor_shapes():
    print_section("5. Registry Schemas Translate To Both Vendor Shapes")

    schemas = build_readonly_registry().schemas()

    anthropic_tools = to_anthropic_tools(schemas)
    openai_tools = to_openai_tools(schemas)
    print(f"  anthropic keys: {sorted(anthropic_tools[0])}")
    print(f"  openai keys:    {sorted(openai_tools[0]['function'])}")

    assert all(set(t) == {"name", "description", "input_schema"} for t in anthropic_tools)
    assert all(t["type"] == "function" for t in openai_tools)
    assert all(set(t["function"]) == {"name", "description", "parameters"} for t in openai_tools)
    # Same tools, same order, whichever vendor is asked.
    assert [t["name"] for t in anthropic_tools] == [t["function"]["name"] for t in openai_tools]


def test_malformed_model_json_arguments_degrade_to_empty_object():
    """OpenAI sends arguments as a JSON string and a model can emit broken
    JSON. That must arrive at the tool layer as ordinary bad arguments."""
    print_section("6. Malformed Argument JSON Becomes An Empty Object")

    for raw in ('{"department": "eng"}', "", "not json at all", "[1,2,3]", '"a string"', None):
        parsed = _safe_json_object(raw)
        print(f"  {raw!r} -> {parsed!r}")
        assert isinstance(parsed, dict)

    assert _safe_json_object('{"department": "eng"}') == {"department": "eng"}
    assert _safe_json_object("not json at all") == {}
    assert _safe_json_object("[1,2,3]") == {}


def test_mock_provider_drives_a_terminating_tool_conversation():
    """The mock is the only tool path CI can run, so its two-turn
    termination is itself a tested property."""
    print_section("7. Mock Provider Requests A Tool, Then Stops")

    provider = LLMProvider()
    schemas = build_readonly_registry().schemas()

    first = provider.generate_with_tools(
        [{"role": "user", "content": "what is the engineering budget?"}], schemas)
    print(f"  turn 1 tool_calls: {[(c['name'], c['arguments']) for c in first['tool_calls']]}")
    assert len(first["tool_calls"]) == 1
    assert first["tool_calls"][0]["name"] == "get_department_budget"
    assert first["tool_calls"][0]["arguments"] == {"department": "engineering"}

    second = provider.generate_with_tools([
        {"role": "user", "content": "what is the engineering budget?"},
        {"role": "assistant", "content": "", "tool_calls": first["tool_calls"]},
        {"role": "tool", "tool_call_id": "x", "name": "get_department_budget", "content": "{}"},
    ], schemas)
    print(f"  turn 2 tool_calls: {second['tool_calls']} content={second['content']!r}")
    assert second["tool_calls"] == []  # terminates once it has a result
    assert second["content"]

    # Topic routing, so the mock exercises more than one tool.
    capacity_turn = provider.generate_with_tools(
        [{"role": "user", "content": "is design at capacity?"}], schemas)
    print(f"  capacity question -> {capacity_turn['tool_calls'][0]['name']}"
          f"({capacity_turn['tool_calls'][0]['arguments']})")
    assert capacity_turn["tool_calls"][0]["name"] == "get_department_capacity"
    assert capacity_turn["tool_calls"][0]["arguments"] == {"department": "design"}


if __name__ == "__main__":
    for _ in range(2):  # pure functions: identical both times
        test_anthropic_merges_parallel_tool_results_into_one_message()
        test_anthropic_omits_empty_assistant_text_block()
        test_anthropic_keeps_assistant_text_when_present()
        test_openai_keeps_tool_results_as_separate_tool_role_messages()
        test_tool_schemas_translate_to_both_vendor_shapes()
        test_malformed_model_json_arguments_degrade_to_empty_object()
        test_mock_provider_drives_a_terminating_tool_conversation()

    print("\n✅ All tool message translation tests passed!\n")
