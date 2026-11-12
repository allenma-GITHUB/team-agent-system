#!/usr/bin/env python3
"""
PerformanceAnalytics.get_top_performers() ranked agents correctly for every
metric, but only ever REPORTED the real value for "quality" and "speed" -
"cost" and "success" came back with a fabricated 0 for every single agent,
regardless of their real cost_per_task/success_rate.

get_top_performers() derived the attribute to report as f"avg_{metric}",
special-cased "speed" (AgentMetrics has avg_duration_hours, not avg_speed),
and let getattr()'s default=0 silently cover the rest. That happened to
work for "quality" (AgentMetrics really does have avg_quality) but
AgentMetrics has no avg_cost or avg_success field - the real fields are
cost_per_task and success_rate - so both of those branches sorted agents
correctly (ranked = sorted(agents, key=lambda a: a.cost_per_task) / ...
a.success_rate) and then reported 0 for every one of them regardless of
order.

Confirmed live before this fix: three agents with real cost_per_task
10.0 / 50.0 / 200.0 and real success_rate 1.0 / 1.0 / 0.5 came back from
get_top_performers("cost", 3) as [("Cheap Agent", 0), ("Mid Agent", 0),
("Expensive Agent", 0)] - correctly ordered, but a value a caller would
read as "no cost data at all" even though real, varying cost data exists.
Same shape for "success". Nothing caught this because every existing
caller (web_server.py's dashboard payload, this module's own
generate_report()) only ever requests "quality", and no prior test
exercised "cost" or "success".

Fixed: get_top_performers() now looks up a fixed metric -> attribute map
(PerformanceAnalytics._METRIC_ATTR) instead of guessing a name and
silently defaulting to 0 when the guess is wrong.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from performance import PerformanceAnalytics

DATA_FILE = "data/test_top_performers_metrics.json"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _fresh_analytics() -> PerformanceAnalytics:
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    return PerformanceAnalytics(data_file=DATA_FILE)


def _seed(analytics: PerformanceAnalytics):
    """Three agents with deliberately distinct cost_per_task/success_rate,
    so a fabricated 0 for every agent cannot be mistaken for a real tie."""
    analytics.record_task("cheap", "Cheap Agent", "engineering",
                           quality=4.0, duration=2.0, cost=10.0, success=True)
    analytics.record_task("mid", "Mid Agent", "engineering",
                           quality=4.0, duration=2.0, cost=50.0, success=True)
    analytics.record_task("expensive", "Expensive Agent", "engineering",
                           quality=4.0, duration=2.0, cost=200.0, success=True)
    analytics.record_task("expensive", "Expensive Agent", "engineering",
                           quality=4.0, duration=2.0, cost=200.0, success=False)


def test_cost_metric_reports_real_cost_per_task():
    print_section("1. get_top_performers('cost', ...) Reports Real Values")
    analytics = _fresh_analytics()
    _seed(analytics)

    expected = {a.name: a.cost_per_task for a in analytics.agent_metrics.values()}
    print(f"  Real cost_per_task: {expected}")

    top_cost = analytics.get_top_performers("cost", 3)
    print(f"  get_top_performers('cost', 3) -> {top_cost}")

    # Cheapest first (ascending), and no name reports a fabricated 0.
    assert [name for name, _ in top_cost] == ["Cheap Agent", "Mid Agent", "Expensive Agent"]
    for name, value in top_cost:
        assert value == expected[name], f"{name}: expected {expected[name]}, got {value}"
    assert all(value != 0 for _, value in top_cost), "no agent's real cost_per_task is 0"


def test_success_metric_reports_real_success_rate():
    print_section("2. get_top_performers('success', ...) Reports Real Values")
    analytics = _fresh_analytics()
    _seed(analytics)

    expected = {a.name: a.success_rate for a in analytics.agent_metrics.values()}
    print(f"  Real success_rate: {expected}")

    top_success = analytics.get_top_performers("success", 3)
    print(f"  get_top_performers('success', 3) -> {top_success}")

    # Highest success first (descending).
    assert [name for name, _ in top_success] == ["Cheap Agent", "Mid Agent", "Expensive Agent"]
    for name, value in top_success:
        assert value == expected[name], f"{name}: expected {expected[name]}, got {value}"
    # The one agent with a real failure must show less than full success,
    # not the same fabricated value as everyone else.
    by_name = dict(top_success)
    assert by_name["Expensive Agent"] < by_name["Cheap Agent"]


def test_quality_and_speed_still_correct():
    """Pin the two metrics that already worked before this fix - the fix
    must not change their behavior at all."""
    print_section("3. 'quality' And 'speed' Are Unaffected")
    analytics = _fresh_analytics()
    _seed(analytics)

    top_quality = analytics.get_top_performers("quality", 3)
    print(f"  get_top_performers('quality', 3) -> {top_quality}")
    for name, value in top_quality:
        assert value == analytics.agent_metrics[
            {"Cheap Agent": "cheap", "Mid Agent": "mid", "Expensive Agent": "expensive"}[name]
        ].avg_quality

    top_speed = analytics.get_top_performers("speed", 3)
    print(f"  get_top_performers('speed', 3) -> {top_speed}")
    for name, value in top_speed:
        assert value == analytics.agent_metrics[
            {"Cheap Agent": "cheap", "Mid Agent": "mid", "Expensive Agent": "expensive"}[name]
        ].avg_duration_hours


def test_unknown_metric_still_returns_empty():
    """Pin the existing, unrelated fallback behavior for an unrecognized
    metric name - must stay [] rather than raise a KeyError now that the
    lookup is a dict instead of an f-string guess."""
    print_section("4. Unknown Metric Still Returns []")
    analytics = _fresh_analytics()
    _seed(analytics)

    result = analytics.get_top_performers("bogus", 3)
    print(f"  get_top_performers('bogus', 3) -> {result}")
    assert result == []


if __name__ == "__main__":
    test_cost_metric_reports_real_cost_per_task()
    test_success_metric_reports_real_success_rate()
    test_quality_and_speed_still_correct()
    test_unknown_metric_still_returns_empty()
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    print("\nAll tests passed!\n")
