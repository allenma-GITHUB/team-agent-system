#!/usr/bin/env python3
"""
llm_provider.py's _mock_tool_response() (the ONLY tool-calling path this
container and the test suite can actually run - no API key is ever
configured, so LLMProvider.select_provider() always falls back to "mock",
and TOOL_CAPABLE_PROVIDERS includes "mock") decides which department a
simulated get_department_budget/get_department_capacity call should ask
about by scanning the user's text with _mock_pick_department(). That
function checked `dept.lower() in text` - plain substring containment, not
a \\b-bounded word match. This is the identical naive-substring bug shape
checkpoints 38/97/98 already fixed at three other sites (route_task(),
infer_required_skills(), estimate_hours()/estimate_complexity()/
estimate_urgency() in departments.py) - "typo" matching inside
"typography", "migration" inside "immigration" - just never swept to this
site. Two of this system's real department names are themselves common
English word-fragments: "support" is a substring of "supported"/
"unsupported"/"supportive", and "product" is a substring of "production"/
"productive"/"reproduction".

Confirmed live before this fix, via the real agent-run pipeline with no
mocking of _mock_pick_department, the tool registry, or LLMProvider itself:

    main_v2.run_agent(
        "Is this decision well-supported? Should finance sign off on the "
        "budget to afford this?"
    )

named "finance" explicitly and no other department, yet the agent's one
tool call was `get_department_budget(department='support')` - looking up
and displaying SUPPORT's budget as the answer to a question about FINANCE
affording something - purely because "supported" contains "support" as a
substring, and department iteration order (config.json's dict order:
engineering, design, support, research, sales, product, finance) checks
"support" long before "finance" ever gets a chance. The tests below drive
the real LLMProvider -> run_agent_loop -> ToolRegistry -> get_department_budget
chain end to end, with isolated budget data, no mocking of the function
under test.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from budgets import BudgetManager
from llm_provider import LLMProvider, _mock_pick_department
from tools import build_readonly_registry
from agent_loop import run_agent_loop


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_mock_pick_department_ignores_substring_false_positives():
    print_section("1. _mock_pick_department() Is Word-Bounded, Not Substring")

    cases = [
        # (text, expected) - each expected department is the one explicitly
        # named; the false-positive substring sits in an unrelated word.
        ("is this decision well-supported, should finance sign off on the spend?", "finance"),
        ("is the research team being productive this sprint? check capacity.", "research"),
        ("can we afford to reproduce last quarter's sales numbers in design?", "design"),
        # Regression guard: a genuine, literal mention of the substring-prone
        # department name must still match it - the fix must not become
        # "never match support/product again."
        ("can support handle the ticket volume this week?", "support"),
        ("what's product's budget look like for this launch?", "product"),
        # No department named at all: falls back to the documented default.
        ("nothing department-specific in this sentence at all", "engineering"),
    ]

    for text, expected in cases:
        picked = _mock_pick_department(text)
        print(f"  {text!r} -> {picked!r} (expected {expected!r})")
        assert picked == expected, (
            f"_mock_pick_department({text!r}) picked {picked!r}, expected {expected!r}"
        )


def test_agent_loop_looks_up_the_named_department_not_a_substring_hit():
    print_section("2. Real agent_loop + ToolRegistry Pipeline Picks The Right Department")

    # Isolated budgets so this test never touches data/budgets.json, per
    # CLAUDE.md's test-isolation convention.
    budgets = BudgetManager(data_file="data/test_mock_department_word_boundary_budgets.json")
    budgets.allocate("support", 5000.0)
    budgets.allocate("finance", 99000.0)
    registry = build_readonly_registry(budgets=budgets)
    provider = LLMProvider()

    question = ("Is this decision well-supported? Should finance sign off "
                "on the budget to afford this?")
    result = run_agent_loop(question, provider, registry, max_iterations=3)

    assert len(result.tool_calls) == 1, f"expected exactly one tool call, got {result.tool_calls}"
    call = result.tool_calls[0]
    print(f"  tool call: {call.name}({call.arguments}) -> {call.result_preview}")

    assert call.name == "get_department_budget"
    assert call.arguments.get("department") == "finance", (
        f"a question naming only 'finance' must look up finance's budget, "
        f"not {call.arguments.get('department')!r} (the false substring-of-"
        f"'supported' hit this bug used to produce)"
    )
    assert call.ok
    assert "99000" in call.result_preview or "99000.0" in call.result_preview, (
        f"expected finance's real $99,000 allocation in the tool result, got: {call.result_preview}"
    )
    assert "5000" not in call.result_preview, (
        f"support's budget must not appear as the answer to a finance question: {call.result_preview}"
    )


if __name__ == "__main__":
    test_mock_pick_department_ignores_substring_false_positives()
    test_agent_loop_looks_up_the_named_department_not_a_substring_hit()
    print("\nAll tests passed!")
