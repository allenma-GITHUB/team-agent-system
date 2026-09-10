#!/usr/bin/env python3
"""
Test that DepartmentHeadAgent actually loads config.json's own per-agent
definitions (skill_level, capabilities, constraints, max_concurrent_tasks)
for its five built-in department heads, instead of always falling back to
a generic stub (skill_level=3, ManagerAgent, minimal capabilities)
regardless of what config.json says. Found while validating the
compensation checkpoint: engineering_head/design_head both showed
identical, generic skill_level=3 profiles despite config.json defining
skill_level=4 for both.
"""
from agent_state import AgentRegistry
from budgets import BudgetManager
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_known_department_head_loads_its_config_profile():
    """engineering_head should load config.json's real skill_level (4),
    not the generic fallback's skill_level (3)."""
    print_section("1. Known Department Head Loads Its config.json Profile")

    registry = AgentRegistry(data_file="data/test_cfg_agents.json")
    budgets = BudgetManager(data_file="data/test_cfg_budgets.json")

    bus = EventBus()
    agent = DepartmentHeadAgent(
        "engineering", llm_provider=LLMProvider(), bus=bus,
        agent_state_registry=registry, budget_manager=budgets
    )

    profile = agent.agent_state.profile
    print(f"  agent_type: {profile.agent_type}, skill_level: {profile.skill_level}, "
          f"max_concurrent_tasks: {profile.max_concurrent_tasks}")
    print(f"  capabilities: {profile.capabilities}")
    print(f"  derived cost_per_hour: ${agent.cost_per_hour}/hr")

    assert profile.skill_level == 4  # config.json's value, not the generic fallback's 3
    assert profile.max_concurrent_tasks == 4  # config.json's value, not the generic fallback's 3
    assert "code_review" in profile.capabilities  # from config.json, not the generic ["delegation", "execution"]
    assert agent.cost_per_hour == 150.0 * (0.6 + 0.2 * 4)  # ManagerAgent base * skill-4 multiplier


def test_two_known_departments_get_different_profiles():
    """engineering_head (max_concurrent_tasks 4) and design_head (3) should
    NOT collapse into the same generic profile - this was the actual bug:
    every department head looked identical regardless of config.json."""
    print_section("2. Different Departments Get Different config.json Profiles")

    registry = AgentRegistry(data_file="data/test_cfg_agents2.json")
    budgets = BudgetManager(data_file="data/test_cfg_budgets2.json")

    bus = EventBus()
    eng = DepartmentHeadAgent("engineering", llm_provider=LLMProvider(), bus=bus,
                              agent_state_registry=registry, budget_manager=budgets)
    design = DepartmentHeadAgent("design", llm_provider=LLMProvider(), bus=bus,
                                  agent_state_registry=registry, budget_manager=budgets)

    print(f"  engineering: max_concurrent_tasks={eng.agent_state.profile.max_concurrent_tasks}")
    print(f"  design: max_concurrent_tasks={design.agent_state.profile.max_concurrent_tasks}")
    assert eng.agent_state.profile.max_concurrent_tasks == 4
    assert design.agent_state.profile.max_concurrent_tasks == 3


def test_unknown_department_still_falls_back_to_the_generic_stub():
    """A department config.json doesn't name (e.g. a test fixture, or a
    custom department someone adds later) should behave exactly as before -
    this is purely additive, not a requirement that every department be
    named in config.json."""
    print_section("3. Unknown Department Falls Back To The Generic Stub")

    registry = AgentRegistry(data_file="data/test_cfg_agents3.json")
    budgets = BudgetManager(data_file="data/test_cfg_budgets3.json")

    bus = EventBus()
    agent = DepartmentHeadAgent(
        "some_future_department", llm_provider=LLMProvider(), bus=bus,
        agent_state_registry=registry, budget_manager=budgets
    )

    profile = agent.agent_state.profile
    print(f"  agent_type: {profile.agent_type}, skill_level: {profile.skill_level}")
    assert profile.agent_type == "ManagerAgent"
    assert profile.skill_level == 3
    assert profile.capabilities == ["delegation", "execution"]


def main():
    print("\n" + "=" * 60)
    print("  CONFIG-DRIVEN AGENT PROFILE LOADING DEMONSTRATION")
    print("=" * 60)

    test_known_department_head_loads_its_config_profile()
    test_two_known_departments_get_different_profiles()
    test_unknown_department_still_falls_back_to_the_generic_stub()

    print("\n" + "=" * 60)
    print("  [OK] All config-agent-profile tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_cfg_agents.json", "data/test_cfg_budgets.json",
              "data/test_cfg_agents2.json", "data/test_cfg_budgets2.json",
              "data/test_cfg_agents3.json", "data/test_cfg_budgets3.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
