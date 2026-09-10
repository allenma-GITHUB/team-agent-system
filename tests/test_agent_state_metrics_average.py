#!/usr/bin/env python3
"""
Regression test for agent_state.py's PerformanceMetrics.update(): it was
computing "averages" as `(running_average + new_value) / 2`, which isn't a
mean at all. After a single task the reported average was already 50% off
the true value (avg = (0.0 + first_value) / 2), and it kept overweighting
whichever value arrived most recently rather than converging on the true
per-task average - unlike performance.py's AgentMetrics, which tracks a
real running mean via total/count.

error_rate had the same shape of bug from the other direction: it was only
recomputed on a *failing* task (`(error_rate + 1) / tasks_completed`), so a
success right after a failure left error_rate stuck at its old (now stale)
value instead of shrinking - understating an agent's real failure rate
whenever a run of failures and successes were interleaved.

This is real, silently-wrong data: TaskExecutor.execute() (task_executor_v2.py)
puts `agent_state.metrics.avg_quality_score` straight into every task's
persisted result, and main_v2.py writes that into tasks.json.

Fixed: incremental-mean formula (avg += (new - avg) / n) for the three
averages, and a real `error_count` field backing error_rate so it recomputes
correctly (error_count / tasks_completed) on every update, not just failures.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import PerformanceMetrics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_single_task_average_equals_the_task_not_half_of_it():
    """The old formula reported avg = (0.0 + 5.0) / 2 = 2.5 after a single
    quality=5.0 task. The average of one data point is that data point."""
    print_section("1. Single-Task Average Is Exact, Not Halved")

    metrics = PerformanceMetrics()
    metrics.update(quality=5.0, hours=2.0, cost=10.0, success=True)

    print(f"  avg_quality_score: {metrics.avg_quality_score} (expected 5.0)")
    print(f"  avg_completion_time_hours: {metrics.avg_completion_time_hours} (expected 2.0)")
    print(f"  avg_cost_per_task: {metrics.avg_cost_per_task} (expected 10.0)")

    assert metrics.avg_quality_score == 5.0
    assert metrics.avg_completion_time_hours == 2.0
    assert metrics.avg_cost_per_task == 10.0


def test_constant_values_converge_to_that_constant_not_below_it():
    """Three tasks all at quality=5.0 must average to 5.0. The old formula
    asymptotically approached but never reached it (3.75 after two, not 5.0)."""
    print_section("2. Repeated Identical Values Average To Themselves")

    metrics = PerformanceMetrics()
    for _ in range(3):
        metrics.update(quality=5.0, hours=4.0, cost=20.0, success=True)

    print(f"  avg_quality_score: {metrics.avg_quality_score} (expected 5.0)")
    assert metrics.avg_quality_score == 5.0
    assert metrics.avg_completion_time_hours == 4.0
    assert metrics.avg_cost_per_task == 20.0


def test_average_matches_true_mean_of_varied_values():
    """A mix of values must match the real arithmetic mean, not whatever
    the recency-biased (old + new) / 2 formula happens to produce."""
    print_section("3. Average Matches True Mean For Varied Values")

    qualities = [5.0, 1.0, 3.0, 4.0]
    metrics = PerformanceMetrics()
    for q in qualities:
        metrics.update(quality=q, hours=1.0, cost=1.0, success=True)

    expected = sum(qualities) / len(qualities)
    print(f"  avg_quality_score: {metrics.avg_quality_score} (expected {expected})")
    assert abs(metrics.avg_quality_score - expected) < 1e-9


def test_error_rate_recomputes_on_success_not_just_on_failure():
    """fail, succeed, succeed, fail -> 2 errors / 4 tasks = 0.5. The old
    formula left error_rate stuck at 1.0 after the first failure (never
    updated on the two successes in between) and then, on the second
    failure, computed (1.0 + 1) / 4 = 0.5 - right here by coincidence, but
    only because the two interleaved successes were exactly canceled out;
    a differently-shaped sequence exposes the staleness directly."""
    print_section("4. error_rate Recomputes On Every Update, Not Just Failures")

    metrics = PerformanceMetrics()
    metrics.update(quality=1.0, hours=1.0, cost=1.0, success=False)  # 1 error / 1 task
    print(f"  after 1 failure: error_rate={metrics.error_rate} (expected 1.0)")
    assert metrics.error_rate == 1.0

    metrics.update(quality=5.0, hours=1.0, cost=1.0, success=True)  # 1 error / 2 tasks
    print(f"  after 1 failure + 1 success: error_rate={metrics.error_rate} (expected 0.5)")
    assert metrics.error_rate == 0.5

    metrics.update(quality=5.0, hours=1.0, cost=1.0, success=True)  # 1 error / 3 tasks
    print(f"  after 1 failure + 2 successes: error_rate={metrics.error_rate} (expected {1/3:.4f})")
    assert abs(metrics.error_rate - (1 / 3)) < 1e-9

    metrics.update(quality=1.0, hours=1.0, cost=1.0, success=False)  # 2 errors / 4 tasks
    print(f"  after 2 failures + 2 successes: error_rate={metrics.error_rate} (expected 0.5)")
    assert metrics.error_rate == 0.5


if __name__ == "__main__":
    test_single_task_average_equals_the_task_not_half_of_it()
    test_constant_values_converge_to_that_constant_not_below_it()
    test_average_matches_true_mean_of_varied_values()
    test_error_rate_recomputes_on_success_not_just_on_failure()

    # Run twice back-to-back to confirm no hidden global/file state.
    test_single_task_average_equals_the_task_not_half_of_it()
    test_constant_values_converge_to_that_constant_not_below_it()
    test_average_matches_true_mean_of_varied_values()
    test_error_rate_recomputes_on_success_not_just_on_failure()

    print("\n✅ All agent_state PerformanceMetrics average tests passed!\n")
