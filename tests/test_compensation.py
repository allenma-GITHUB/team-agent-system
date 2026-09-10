#!/usr/bin/env python3
"""
Test AgentProfile.hourly_rate() and its wiring into DepartmentHeadAgent:
compensation now derives from agent_type/skill_level instead of every
agent billing at a flat $100/hr regardless of seniority.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentRegistry
from budgets import BudgetManager
from core import EventBus
from llm_provider import LLMProvider
from task_executor_v2 import DepartmentHeadAgent


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_higher_seniority_types_earn_more():
    print_section("1. Higher-Seniority Agent Types Earn More")

    rates = {}
    for agent_type in ["CoordinatorAgent", "ManagerAgent", "SpecialistAgent", "LeaderAgent"]:
        profile = AgentProfile(
            agent_id="x", name="X", agent_type=agent_type, department="d",
            expertise_areas=[], skill_level=3, capabilities=[], constraints=[]
        )
        rates[agent_type] = profile.hourly_rate()
        print(f"  {agent_type} (skill 3): ${rates[agent_type]}/hr")

    assert rates["CoordinatorAgent"] < rates["ManagerAgent"] < rates["SpecialistAgent"] < rates["LeaderAgent"]


def test_higher_skill_level_earns_more_within_the_same_type():
    print_section("2. Higher Skill Level Earns More Within The Same Type")

    rates = {}
    for skill in [1, 2, 3, 4, 5]:
        profile = AgentProfile(
            agent_id="x", name="X", agent_type="SpecialistAgent", department="d",
            expertise_areas=[], skill_level=skill, capabilities=[], constraints=[]
        )
        rates[skill] = profile.hourly_rate()
        print(f"  SpecialistAgent skill {skill}: ${rates[skill]}/hr")

    assert rates[1] < rates[2] < rates[3] < rates[4] < rates[5]


def test_unknown_agent_type_falls_back_to_flat_rate():
    print_section("3. Unknown agent_type Falls Back Cleanly")

    profile = AgentProfile(
        agent_id="x", name="X", agent_type="SomeFutureAgentType", department="d",
        expertise_areas=[], skill_level=3, capabilities=[], constraints=[]
    )
    rate = profile.hourly_rate()
    print(f"  Unrecognized type, skill 3: ${rate}/hr")
    assert rate > 0  # doesn't crash or return 0/negative for an unmapped type


def test_department_head_bills_at_its_profile_rate_by_default():
    """A DepartmentHeadAgent with no explicit cost_per_hour should bill at
    its own agent_state's derived rate, not a flat number."""
    print_section("4. DepartmentHeadAgent Bills At Its Profile's Rate")

    registry = AgentRegistry(data_file="data/test_comp_agents.json")
    budgets = BudgetManager(data_file="data/test_comp_budgets.json")
    budgets.allocate("qa_comp_dept", 10000)

    profile = AgentProfile(
        agent_id="qa_comp_dept_head", name="Senior Lead", agent_type="SpecialistAgent",
        department="qa_comp_dept", expertise_areas=["qa_comp_dept"], skill_level=5,
        capabilities=[], constraints=[]
    )
    agent_state = registry.register(profile)

    bus = EventBus()
    agent = DepartmentHeadAgent(
        "qa_comp_dept", agent_state=agent_state, llm_provider=LLMProvider(), bus=bus,
        agent_state_registry=registry, budget_manager=budgets
    )
    print(f"  Derived cost_per_hour: ${agent.cost_per_hour}/hr (expected: ${profile.hourly_rate()})")
    assert agent.cost_per_hour == profile.hourly_rate()

    agent.run("A task billed at this agent's own rate", estimated_hours=2.0)
    budget = budgets.get_budget("qa_comp_dept")
    print(f"  Spent: ${budget.spent:,.2f} (2h * ${profile.hourly_rate()}/hr expected)")
    assert budget.spent == 2.0 * profile.hourly_rate()


def test_explicit_cost_per_hour_still_overrides_the_derived_rate():
    print_section("5. Explicit cost_per_hour Still Overrides The Derived Rate")

    registry = AgentRegistry(data_file="data/test_comp_agents2.json")
    budgets = BudgetManager(data_file="data/test_comp_budgets2.json")
    budgets.allocate("qa_comp_override", 10000)

    bus = EventBus()
    agent = DepartmentHeadAgent(
        "qa_comp_override", llm_provider=LLMProvider(), bus=bus, cost_per_hour=42,
        agent_state_registry=registry, budget_manager=budgets
    )
    print(f"  Explicit cost_per_hour: ${agent.cost_per_hour}/hr")
    assert agent.cost_per_hour == 42


def main():
    print("\n" + "=" * 60)
    print("  AGENT COMPENSATION DEMONSTRATION")
    print("=" * 60)

    test_higher_seniority_types_earn_more()
    test_higher_skill_level_earns_more_within_the_same_type()
    test_unknown_agent_type_falls_back_to_flat_rate()
    test_department_head_bills_at_its_profile_rate_by_default()
    test_explicit_cost_per_hour_still_overrides_the_derived_rate()

    print("\n" + "=" * 60)
    print("  [OK] All compensation tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    for f in ["data/test_comp_agents.json", "data/test_comp_budgets.json",
              "data/test_comp_agents2.json", "data/test_comp_budgets2.json"]:
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
