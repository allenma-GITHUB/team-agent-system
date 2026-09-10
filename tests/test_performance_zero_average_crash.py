#!/usr/bin/env python3
"""
Regression test for a real crash found while re-checking checkpoint 25's
consequences: allowing `--hours 0` as a legitimate estimate (a genuinely
free/instant task) made every recorded duration for that agent exactly
0.0. performance.py's get_system_metrics()/DepartmentMetrics.
compute_from_agents()/get_recommendations() all computed averages with a
`statistics.mean([a.X for a in agents if a.X > 0])` filter meant to skip
"no data yet" agents (whose metrics default to 0.0 before any task) - but
it can't tell that apart from "this agent has real data whose average is
genuinely 0", and when every agent's value happens to be exactly 0, the
filtered list is empty and statistics.mean([]) raises StatisticsError.

Confirmed via a real repro: submit a --hours 0 task, process it, then run
`report` - main_v2.py's show_report() crashed with an uncaught
StatisticsError instead of printing a report.

Fixed: dropped the "> 0" filter everywhere it appeared. Every AgentMetrics
entry is only ever created inside record_task(), immediately followed by
.update() (which increments tasks_completed to at least 1) - so every
agent already reachable in these functions has real data, and the filter
was never actually distinguishing "no data" from "zero average"; it was
only ever wrongly excluding the latter (which also silently skewed the
mean upward whenever some agents had a real zero value alongside others
that didn't).
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_report_does_not_crash_when_every_agent_has_zero_duration():
    print_section("1. generate_report() Survives All-Zero-Duration Metrics")

    analytics = PerformanceAnalytics(data_file="data/test_perf_zero_avg.json")
    analytics.record_task("zero_agent", "Zero-Hour Agent", "engineering",
                           quality=4.0, duration=0.0, cost=0.0, success=True)

    report = analytics.generate_report()
    print(report)
    assert "SYSTEM PERFORMANCE REPORT" in report
    assert "Avg Turnaround: 0.0h" in report


def test_get_system_metrics_does_not_crash_and_reports_a_real_zero():
    print_section("2. get_system_metrics() Reports A Genuine Zero Average, Doesn't Crash")

    analytics = PerformanceAnalytics(data_file="data/test_perf_zero_avg2.json")
    analytics.record_task("zero_agent", "Zero-Hour Agent", "engineering",
                           quality=4.0, duration=0.0, cost=0.0, success=True)

    system = analytics.get_system_metrics()
    print(f"  avg_turnaround_time: {system.avg_turnaround_time}")
    assert system.avg_turnaround_time == 0.0
    assert system.avg_quality == 4.0


def test_zero_and_nonzero_agents_average_correctly_not_skewed():
    print_section("3. A Real Zero Average No Longer Skews The Mean Upward")

    analytics = PerformanceAnalytics(data_file="data/test_perf_zero_avg3.json")
    analytics.record_task("zero_agent", "Zero-Hour Agent", "engineering",
                           quality=4.0, duration=0.0, cost=0.0, success=True)
    analytics.record_task("normal_agent", "Normal Agent", "engineering",
                           quality=4.0, duration=4.0, cost=100.0, success=True)

    system = analytics.get_system_metrics()
    print(f"  avg_turnaround_time (should be (0+4)/2=2.0, not 4.0): {system.avg_turnaround_time}")
    assert system.avg_turnaround_time == 2.0  # both agents counted, not just the nonzero one

    dept = analytics.get_department_metrics("engineering")
    print(f"  department avg_cost_per_task (should be (0+100)/2=50.0): {dept.avg_cost_per_task}")
    assert dept.avg_cost_per_task == 50.0


def test_get_recommendations_does_not_crash_when_every_agent_has_zero_duration():
    print_section("4. get_recommendations() Survives All-Zero-Duration Metrics")

    analytics = PerformanceAnalytics(data_file="data/test_perf_zero_avg4.json")
    analytics.record_task("zero_agent", "Zero-Hour Agent", "engineering",
                           quality=4.0, duration=0.0, cost=0.0, success=True)

    recs = analytics.get_recommendations()
    print(f"  recommendations: {recs}")  # just needs to not raise


def main():
    print("\n" + "=" * 60)
    print("  PERFORMANCE ZERO-AVERAGE CRASH REGRESSION TEST")
    print("=" * 60)

    test_report_does_not_crash_when_every_agent_has_zero_duration()
    test_get_system_metrics_does_not_crash_and_reports_a_real_zero()
    test_zero_and_nonzero_agents_average_correctly_not_skewed()
    test_get_recommendations_does_not_crash_when_every_agent_has_zero_duration()

    print("\n" + "=" * 60)
    print("  [OK] All performance zero-average tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_perf_zero_avg*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
