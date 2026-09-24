#!/usr/bin/env python3
"""
Checkpoint 51: `SystemMetrics.bottleneck_department` was declared on the
dataclass but `get_system_metrics()` never assigned it - always `None`
regardless of how much real data existed, a dead field first flagged in
checkpoint 50's own Next Steps. `bottleneck_agent`, the field next to it,
had the opposite problem: correctly computed since its introduction, but
never printed by `generate_report()` (main_v2.show_report()'s CLI output)
or read by the dashboard - `web_server.build_report_payload()` shipped it
in every `/api/report` response's `system` object via `asdict()`, but no
consumer ever displayed it. Confirmed both live before touching anything:

    system.bottleneck_department: None            (always, any input)
    "Bottleneck" in generate_report() output: False

Fixed: get_system_metrics() now groups agents by department and picks the
one with the highest mean avg_duration_hours - the same "slowest wins"
rule bottleneck_agent already used one level down (DepartmentMetrics
doesn't track turnaround at all, so this can't just call
get_department_metrics()). generate_report() now prints both fields with
their real backing numbers; dashboard.html's Performance panel now reads
sys.bottleneck_agent/sys.bottleneck_department off the same /api/report
response it already fetches.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_bottleneck_department_was_dead_now_computes_the_slowest_department():
    print_section("1. bottleneck_department Is Computed, Not Always None")

    analytics = PerformanceAnalytics(data_file="data/test_bottleneck_dept1.json")
    analytics.record_task("eng_lead", "Engineering Lead", "engineering",
                           quality=4.5, duration=2.0, cost=50.0, success=True)
    analytics.record_task("design_lead", "Design Lead", "design",
                           quality=4.2, duration=6.0, cost=80.0, success=True)
    analytics.record_task("research_lead", "Research Lead", "research",
                           quality=4.0, duration=3.0, cost=60.0, success=True)

    system = analytics.get_system_metrics()
    print(f"  bottleneck_agent: {system.bottleneck_agent}")
    print(f"  bottleneck_department: {system.bottleneck_department}")
    assert system.bottleneck_agent == "design_lead"
    assert system.bottleneck_department == "design"


def test_bottleneck_department_uses_the_department_mean_not_just_the_slowest_agent():
    print_section("2. bottleneck_department Uses A Real Department Mean")

    analytics = PerformanceAnalytics(data_file="data/test_bottleneck_dept2.json")
    # engineering: two agents averaging 6h (1h, 11h) - beats design's lone 5h agent
    # on department mean, even though the single slowest agent overall is eng2.
    analytics.record_task("eng1", "Eng One", "engineering", quality=4.0, duration=1.0, cost=10.0, success=True)
    analytics.record_task("eng2", "Eng Two", "engineering", quality=4.0, duration=11.0, cost=10.0, success=True)
    analytics.record_task("design1", "Design One", "design", quality=4.0, duration=5.0, cost=10.0, success=True)

    system = analytics.get_system_metrics()
    print(f"  bottleneck_agent: {system.bottleneck_agent} (single slowest agent)")
    print(f"  bottleneck_department: {system.bottleneck_department} (highest department mean)")
    assert system.bottleneck_agent == "eng2"
    assert system.bottleneck_department == "engineering"


def test_bottleneck_department_stays_none_with_no_data():
    print_section("3. No Agents Yet -> Stays None, Not A Crash Or A Fake Value")

    analytics = PerformanceAnalytics(data_file="data/test_bottleneck_dept3.json")
    system = analytics.get_system_metrics()
    assert system.bottleneck_agent is None
    assert system.bottleneck_department is None


def test_generate_report_prints_both_bottleneck_fields_with_real_numbers():
    print_section("4. generate_report() Prints Bottleneck Agent And Department")

    analytics = PerformanceAnalytics(data_file="data/test_bottleneck_dept4.json")
    analytics.record_task("eng_lead", "Engineering Lead", "engineering",
                           quality=4.5, duration=2.0, cost=50.0, success=True)
    analytics.record_task("design_lead", "Design Lead", "design",
                           quality=4.2, duration=6.0, cost=80.0, success=True)

    report = analytics.generate_report()
    print(report)
    assert "Bottleneck Agent: Design Lead (6.0h avg)" in report
    assert "Bottleneck Department: design (6.0h avg)" in report


def test_generate_report_omits_bottleneck_lines_with_no_data():
    print_section("5. No Data Yet -> No Bottleneck Lines In The CLI Report")

    analytics = PerformanceAnalytics(data_file="data/test_bottleneck_dept5.json")
    report = analytics.generate_report()
    assert "Bottleneck Agent" not in report
    assert "Bottleneck Department" not in report


def main():
    print("\n" + "=" * 60)
    print("  BOTTLENECK_DEPARTMENT DEAD-FIELD REGRESSION TEST")
    print("=" * 60)

    test_bottleneck_department_was_dead_now_computes_the_slowest_department()
    test_bottleneck_department_uses_the_department_mean_not_just_the_slowest_agent()
    test_bottleneck_department_stays_none_with_no_data()
    test_generate_report_prints_both_bottleneck_fields_with_real_numbers()
    test_generate_report_omits_bottleneck_lines_with_no_data()

    print("\n" + "=" * 60)
    print("  [OK] All bottleneck_department tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_bottleneck_dept*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
