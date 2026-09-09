#!/usr/bin/env python3
"""
Test performance tracking and analytics system.
"""
from performance import PerformanceAnalytics


def test_performance_tracking():
    """Demonstrate performance tracking."""
    print("\n" + "="*60)
    print("Performance Analytics Test")
    print("="*60)

    analytics = PerformanceAnalytics()

    # Simulate task results for different agents
    agents = [
        ("eng_lead", "Engineering Lead", "engineering"),
        ("design_lead", "Design Lead", "design"),
        ("research_lead", "Research Lead", "research"),
    ]

    print("\nSimulating task execution results...")

    # Engineering: high quality, good speed
    for i in range(5):
        quality = 4.5 + (i % 2) * 0.3
        duration = 2.0 + i * 0.5
        cost = 100 + i * 10
        analytics.record_task(*agents[0], quality, duration, cost, success=(i < 4))
    print(f"  [OK] Engineering: 5 tasks")

    # Design: high quality, slower
    for i in range(4):
        quality = 4.2 + (i % 2) * 0.2
        duration = 4.0 + i * 0.5
        cost = 150 + i * 15
        analytics.record_task(*agents[1], quality, duration, cost, success=True)
    print(f"  [OK] Design: 4 tasks")

    # Research: good quality, variable speed
    for i in range(6):
        quality = 3.8 + (i % 3) * 0.3
        duration = 3.0 + i * 1.0
        cost = 120 + i * 12
        analytics.record_task(*agents[2], quality, duration, cost, success=(i < 5))
    print(f"  [OK] Research: 6 tasks")

    # Get system metrics
    print("\n[System Metrics]")
    system = analytics.get_system_metrics()
    print(f"  Total Tasks: {system.total_tasks}")
    print(f"  Avg Quality: {system.avg_quality:.2f}/5.0")
    print(f"  Success Rate: {system.system_success_rate:.0%}")
    print(f"  Total Cost: ${system.total_cost:,.2f}")

    # Top performers
    print("\n[Top Performers]")
    for metric in ["quality", "speed"]:
        print(f"\n  By {metric.title()}:")
        top = analytics.get_top_performers(metric, 3)
        for name, score in top:
            if metric == "speed":
                print(f"    - {name}: {score:.1f}h avg")
            else:
                print(f"    - {name}: {score:.2f}/5.0")

    # Recommendations
    print("\n[Recommendations]")
    recs = analytics.get_recommendations()
    if recs:
        for rec in recs:
            print(f"  - {rec['agent']}: {rec['action']}")
    else:
        print("  [OK] No recommendations at this time")

    # Full report
    print(analytics.generate_report())


if __name__ == "__main__":
    test_performance_tracking()
