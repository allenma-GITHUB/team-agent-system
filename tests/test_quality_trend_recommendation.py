#!/usr/bin/env python3
"""
AgentMetrics.get_quality_trend() has existed in performance.py since early
in this file's history and had exactly zero callers anywhere in the
codebase - not generate_report(), not get_recommendations(), not any web
consumer, not even a test. It computes a real signal avg_quality
structurally cannot: avg_quality is an all-time mean, so an agent who was
excellent for a long stretch and has been sliding for their last few tasks
still reads as "fine" right up until the decline has already dragged the
average down with it.

Confirmed live before fixing: an agent with 5 strong scores (~4.8) followed
by 5 weak ones (~3.0) ends up with avg_quality=3.88 (comfortably above any
threshold get_recommendations() checks) and success_rate=1.0/avg_duration
unremarkable - zero recommendations fired - while
get_quality_trend() already read (-1.76, "declining") the whole time.

Fixed: get_recommendations() now calls get_quality_trend() per agent and
emits a "quality_decline" recommendation whenever the recent trend reads
"declining", regardless of what the all-time average says. No new wiring
needed beyond that: get_recommendations()'s output already reaches both
main_v2.show_report()'s CLI "Recommendations" section and the dashboard's
#recs-list (static/dashboard.html's loadReport(), which renders every
{agent, issue, action} dict in report.recommendations uniformly) - this
closes the same "real computed value, no consumer" gap shape as checkpoint
51's bottleneck_department fix, one level down (a method with zero
callers, not a dataclass field never assigned).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_declining_trend_recommended_even_with_a_healthy_average():
    print_section("1. Declining Recent Quality Surfaces Despite A Fine Average")

    analytics = PerformanceAnalytics(data_file="data/test_quality_trend1.json")
    scores = [4.8, 4.7, 4.9, 4.6, 4.8, 3.2, 3.0, 2.8, 3.1, 2.9]
    for s in scores:
        analytics.record_task("a1", "Alice", "engineering",
                               quality=s, duration=1.0, cost=10.0, success=True)

    agent = analytics.get_agent_metrics("a1")
    print(f"  avg_quality (all-time): {agent.avg_quality:.2f}")
    print(f"  success_rate: {agent.success_rate}")
    print(f"  get_quality_trend(): {agent.get_quality_trend()}")
    assert agent.avg_quality > 3.5, "fixture should look fine on the all-time average"
    assert agent.success_rate == 1.0, "fixture should not trip the training_needed check"

    recs = analytics.get_recommendations()
    print(f"  recommendations: {recs}")
    decline_recs = [r for r in recs if r["type"] == "quality_decline"]
    assert len(decline_recs) == 1
    assert decline_recs[0]["agent"] == "Alice"


def test_stable_or_improving_quality_recommends_nothing():
    print_section("2. Stable/Improving Quality -> No quality_decline Recommendation")

    analytics = PerformanceAnalytics(data_file="data/test_quality_trend2.json")
    scores = [3.0, 3.1, 3.0, 3.2, 3.1, 4.5, 4.6, 4.4, 4.7, 4.8]
    for s in scores:
        analytics.record_task("a2", "Bob", "engineering",
                               quality=s, duration=1.0, cost=10.0, success=True)

    agent = analytics.get_agent_metrics("a2")
    print(f"  get_quality_trend(): {agent.get_quality_trend()}")
    assert agent.get_quality_trend()[1] == "improving"

    recs = analytics.get_recommendations()
    decline_recs = [r for r in recs if r["type"] == "quality_decline"]
    assert decline_recs == []


def test_insufficient_data_recommends_nothing():
    print_section("3. Fewer Than One Window Of Tasks -> No Recommendation, No Crash")

    analytics = PerformanceAnalytics(data_file="data/test_quality_trend3.json")
    analytics.record_task("a3", "Cara", "engineering",
                           quality=2.0, duration=1.0, cost=10.0, success=True)
    analytics.record_task("a3", "Cara", "engineering",
                           quality=1.5, duration=1.0, cost=10.0, success=True)

    agent = analytics.get_agent_metrics("a3")
    assert agent.get_quality_trend()[1] == "insufficient_data"

    recs = analytics.get_recommendations()
    decline_recs = [r for r in recs if r["type"] == "quality_decline"]
    assert decline_recs == []


def test_quality_decline_reaches_the_cli_report_text():
    print_section("4. generate_report() Prints The Decline, Not Just get_recommendations()")

    analytics = PerformanceAnalytics(data_file="data/test_quality_trend4.json")
    scores = [4.8, 4.7, 4.9, 4.6, 4.8, 3.2, 3.0, 2.8, 3.1, 2.9]
    for s in scores:
        analytics.record_task("a4", "Dana", "engineering",
                               quality=s, duration=1.0, cost=10.0, success=True)

    report = analytics.generate_report()
    print(report)
    # generate_report()'s Recommendations section prints `rec['action']`,
    # not `rec['issue']` (see performance.py) - the action text, not the
    # "trending down" phrasing, is what actually reaches the CLI.
    assert "Dana: Review Dana's recent work for a cause" in report


def main():
    print("\n" + "=" * 60)
    print("  QUALITY TREND RECOMMENDATION REGRESSION TEST")
    print("=" * 60)

    test_declining_trend_recommended_even_with_a_healthy_average()
    test_stable_or_improving_quality_recommends_nothing()
    test_insufficient_data_recommends_nothing()
    test_quality_decline_reaches_the_cli_report_text()

    print("\n" + "=" * 60)
    print("  [OK] All quality trend recommendation tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_quality_trend*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
