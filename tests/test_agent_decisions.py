#!/usr/bin/env python3
"""
Test script demonstrating autonomous agent decision-making.
Shows agents making smart decisions about task execution, delegation, and escalation.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentProfile, AgentState, agent_registry
from agent_decisions import AgentDecisionEngine, DecisionContext, OrganizationDecisionMaker
import json


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def demo_agent_profiles():
    """Demonstrate agent profiles and registry."""
    print_section("1. Agent Profiles & Registry")

    # Create some agents
    profiles = [
        AgentProfile(
            agent_id="eng_lead",
            name="Engineering Lead",
            agent_type="ManagerAgent",
            department="engineering",
            expertise_areas=["architecture", "backend", "database"],
            skill_level=5,
            capabilities=["code_review", "delegation"],
            constraints=["cannot_execute_alone"],
            max_concurrent_tasks=3
        ),
        AgentProfile(
            agent_id="design_lead",
            name="Design Lead",
            agent_type="ManagerAgent",
            department="design",
            expertise_areas=["ui_design", "ux_research"],
            skill_level=4,
            capabilities=["design_review", "delegation"],
            constraints=[],
            max_concurrent_tasks=2
        ),
        AgentProfile(
            agent_id="ceo",
            name="CEO",
            agent_type="LeaderAgent",
            department="leadership",
            expertise_areas=["strategy", "budgeting"],
            skill_level=5,
            capabilities=["approve_decisions", "budget_allocation"],
            constraints=[],
            max_concurrent_tasks=5
        )
    ]

    for profile in profiles:
        state = agent_registry.register(profile)
        print(f"[OK] Registered {state.profile.name} ({state.profile.agent_type})")
        print(f"  Skills: {', '.join(state.profile.expertise_areas)}")
        print(f"  Constraints: {state.profile.constraints or 'None'}")
        print()


def demo_decision_making():
    """Demonstrate autonomous decision-making."""
    print_section("2. Autonomous Decision-Making")

    eng_lead = agent_registry.get("eng_lead")

    # Scenario 1: Simple task that agent can execute
    print("Scenario 1: Simple code review task")
    context1 = DecisionContext(
        task_id="task_001",
        task_type="code_review",
        required_skills=["backend"],
        complexity=0.3,
        urgency=0.5,
        estimated_hours=2,
        required_approval_level=2
    )

    engine = AgentDecisionEngine(eng_lead)
    decision1 = engine.decide(context1)
    print(f"  Decision: {decision1.decision}")
    print(f"  Confidence: {decision1.confidence:.2%}")
    print(f"  Reasoning: {decision1.reasoning}\n")

    # Scenario 2: Complex task requiring approval
    print("Scenario 2: Major architecture redesign")
    context2 = DecisionContext(
        task_id="task_002",
        task_type="architecture",
        required_skills=["architecture"],
        complexity=0.9,
        urgency=0.8,
        estimated_hours=40,
        required_approval_level=4
    )

    decision2 = engine.decide(context2)
    print(f"  Decision: {decision2.decision}")
    print(f"  Approval Required: {decision2.approval_required}")
    print(f"  Confidence: {decision2.confidence:.2%}")
    print(f"  Reasoning: {decision2.reasoning}\n")


def demo_performance_tracking():
    """Demonstrate performance metrics tracking."""
    print_section("3. Performance Tracking & Learning")

    design_lead = agent_registry.get("design_lead")

    # Simulate task results
    print("Simulating 3 design tasks...")
    for i in range(3):
        quality = 4.5 if i < 2 else 2.0  # Last one performed poorly
        hours = 2.0 + i
        cost = 50.0 + (i * 10)

        design_lead.record_performance(quality, hours, cost, success=(quality >= 3.0))
        print(f"  Task {i+1}: Quality={quality}, Hours={hours}, Cost=${cost:.2f}")

    print(f"\nPerformance Summary:")
    metrics = design_lead.metrics
    print(f"  Tasks Completed: {metrics.tasks_completed}")
    print(f"  Avg Quality: {metrics.avg_quality_score:.1f}/5.0")
    print(f"  Avg Time: {metrics.avg_completion_time_hours:.1f} hours")
    print(f"  Error Rate: {metrics.error_rate:.1%}")
    print()

    # Show learned preferences
    design_lead.learn_preference("ui_design", 0.8)  # Loves UI design
    design_lead.learn_preference("documentation", -0.5)  # Dislikes docs
    print(f"Learned Preferences:")
    print(f"  UI Design: {design_lead.learned_preferences.get('ui_design', 0):.1f}")
    print(f"  Documentation: {design_lead.learned_preferences.get('documentation', 0):.1f}\n")


def demo_delegation():
    """Demonstrate smart delegation."""
    print_section("4. Smart Delegation")

    # Get the engineering lead (overloaded)
    eng_lead = agent_registry.get("eng_lead")
    eng_lead.current_workload = eng_lead.profile.max_concurrent_tasks  # Fully loaded

    print(f"Engineering Lead workload: {eng_lead.current_workload}/{eng_lead.profile.max_concurrent_tasks}")

    # Find alternatives
    candidates = agent_registry.available_agents(skill_required="backend")
    print(f"Available agents for 'backend' work: {len(candidates)}")

    if candidates:
        for candidate in candidates:
            print(f"  - {candidate.profile.name}: Workload {candidate.current_workload}/{candidate.profile.max_concurrent_tasks}")
    print()


def demo_relationships():
    """Demonstrate agent relationships and trust."""
    print_section("5. Agent Relationships & Trust")

    eng_lead = agent_registry.get("eng_lead")
    design_lead = agent_registry.get("design_lead")

    print(f"Simulating collaboration between {eng_lead.profile.name} and {design_lead.profile.name}...")

    # Record successful collaboration
    eng_lead.update_trust_with_agent(design_lead.profile.agent_id, 0.3)
    print(f"  After successful collaboration: trust +0.3")

    # Check trust score
    trust_score = eng_lead.relationships[design_lead.profile.agent_id].trust_score
    print(f"  New trust score: {trust_score:.2f}\n")


def demo_org_approval():
    """Demonstrate organizational decision approval."""
    print_section("6. Organizational Approval & Conflict Resolution")

    ceo = agent_registry.get("ceo")
    maker = OrganizationDecisionMaker(ceo.profile.agent_id)

    # Test approval decision
    from agent_decisions import DecisionResult

    high_risk_decision = DecisionResult(
        decision="execute",
        assigned_agent_id="eng_lead",
        reasoning="Architectural change",
        confidence=0.6,
        approval_required=True
    )

    budget = 10000.0
    approved = maker.approve_decision(high_risk_decision, budget)
    print(f"High-risk decision approval (budget: ${budget:,.0f}): {approved}\n")


def demo_persistence():
    """Demonstrate persistence to JSON."""
    print_section("7. Persistence & State Storage")

    agent_registry.save()
    print("[OK] Agent states saved to: data/agent_states.json")

    # Show file content sample
    import json
    from pathlib import Path
    agent_file = Path("data/agent_states.json")
    if agent_file.exists():
        with open(agent_file) as f:
            data = json.load(f)
            first_agent_id = list(data.keys())[0]
            first_agent = data[first_agent_id]
            print(f"\nSample - {first_agent_id}:")
            print(f"  Type: {first_agent['profile']['agent_type']}")
            print(f"  Metrics: {first_agent['metrics']['tasks_completed']} tasks completed")
            print(f"  File size: {agent_file.stat().st_size} bytes\n")


def main():
    """Run all demonstrations."""
    print("\n" + "="*60)
    print("  AGENT DECISION-MAKING DEMONSTRATION")
    print("="*60)

    demo_agent_profiles()
    demo_decision_making()
    demo_performance_tracking()
    demo_delegation()
    demo_relationships()
    demo_org_approval()
    demo_persistence()

    print("="*60)
    print("  [OK] All demonstrations complete!")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
