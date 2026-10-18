#!/usr/bin/env python3
"""
Checkpoint 70: RelationshipScore.collaboration_count/last_collaboration have
been written on every real trust update since delegation feedback went live
(checkpoint 61's update_trust_with_agent()) - but nothing ever read either
field back. grep for both outside their own declaration/write site in
agent_state.py turned up only test assertions (test_delegation_trust_feedback.py
checks trust_score, never the other two fields) - no report, no dashboard,
no decision path.

Confirmed live before this fix: ten real delegations between two agents drove
trust_score to 1.0 and collaboration_count to 10, and generate_report()'s
output ("ollaborat"/"rust" substring search) was unchanged either way -
identical to zero collaborations ever having happened.

Fixed: AgentRegistry.collaboration_summary() reads every agent's
relationships dict back into a flat, directional list (one entry per
agent-pair - update_trust_with_agent() is only ever called on the
delegator's own AgentState, so these are not symmetric pairs).
PerformanceAnalytics.get_collaboration_summary() wraps it (same
registry-injection shape as get_resource_summary()'s budgets/capacity),
generate_report() prints an "Agent Trust & Collaboration" section only when
at least one relationship exists, build_report_payload() carries it to
/api/report, and dashboard.html renders a matching panel.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_state import AgentRegistry, AgentProfile
from performance import PerformanceAnalytics


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _make_profile(agent_id: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id, name=agent_id.title(), agent_type="ManagerAgent",
        department="engineering", expertise_areas=["backend"], skill_level=3,
        capabilities=[], constraints=[]
    )


def test_collaboration_summary_empty_with_no_relationships():
    print_section("1. collaboration_summary() Is Empty With No Relationships")

    registry = AgentRegistry(data_file="data/test_collab_summary1.json")
    registry.register(_make_profile("qa_collab_a"))
    registry.register(_make_profile("qa_collab_b"))

    assert registry.collaboration_summary() == []


def test_collaboration_summary_reads_real_recorded_history():
    print_section("2. collaboration_summary() Reads Real trust_score/collaboration_count/last_collaboration")

    registry = AgentRegistry(data_file="data/test_collab_summary2.json")
    a = registry.register(_make_profile("qa_collab_a"))
    registry.register(_make_profile("qa_collab_b"))

    for _ in range(10):
        a.update_trust_with_agent("qa_collab_b", 0.05)

    summary = registry.collaboration_summary()
    print(f"  summary: {summary}")
    assert len(summary) == 1
    rel = summary[0]
    assert rel["from_agent"] == "qa_collab_a"
    assert rel["to_agent"] == "qa_collab_b"
    assert rel["collaboration_count"] == 10
    assert rel["trust_score"] == a.relationships["qa_collab_b"].trust_score
    assert rel["last_collaboration"] is not None


def test_collaboration_summary_is_directional_not_a_deduplicated_pair():
    print_section("3. Relationships Are Directional - A->B And B->A Are Separate Entries")

    registry = AgentRegistry(data_file="data/test_collab_summary3.json")
    a = registry.register(_make_profile("qa_collab_a"))
    b = registry.register(_make_profile("qa_collab_b"))

    a.update_trust_with_agent("qa_collab_b", 0.1)
    b.update_trust_with_agent("qa_collab_a", -0.2)

    summary = registry.collaboration_summary()
    pairs = {(rel["from_agent"], rel["to_agent"]) for rel in summary}
    print(f"  pairs: {pairs}")
    assert pairs == {("qa_collab_a", "qa_collab_b"), ("qa_collab_b", "qa_collab_a")}


def test_get_collaboration_summary_sorts_by_collaboration_count():
    print_section("4. get_collaboration_summary() Ranks Most-Collaborative First")

    registry = AgentRegistry(data_file="data/test_collab_summary4.json")
    a = registry.register(_make_profile("qa_collab_a"))
    registry.register(_make_profile("qa_collab_b"))
    registry.register(_make_profile("qa_collab_c"))

    a.update_trust_with_agent("qa_collab_b", 0.1)  # 1 collaboration
    for _ in range(5):
        a.update_trust_with_agent("qa_collab_c", 0.05)  # 5 collaborations

    analytics = PerformanceAnalytics(data_file="data/test_collab_summary4_metrics.json")
    summary = analytics.get_collaboration_summary(registry=registry)
    print(f"  summary: {summary}")
    assert [rel["to_agent"] for rel in summary] == ["qa_collab_c", "qa_collab_b"]


def test_generate_report_prints_trust_and_collaboration_with_real_data():
    print_section("5. generate_report() Prints Agent Trust & Collaboration")

    registry = AgentRegistry(data_file="data/test_collab_summary5.json")
    a = registry.register(_make_profile("qa_collab_a"))
    registry.register(_make_profile("qa_collab_b"))
    for _ in range(3):
        a.update_trust_with_agent("qa_collab_b", 0.1)

    analytics = PerformanceAnalytics(data_file="data/test_collab_summary5_metrics.json")
    report = analytics.generate_report(registry=registry)
    print(report)

    assert "Agent Trust & Collaboration:" in report
    assert "qa_collab_a -> qa_collab_b" in report
    assert "3 collaborations" in report


def test_generate_report_omits_section_with_no_relationships():
    print_section("6. No Relationships Yet -> No Agent Trust & Collaboration Section")

    registry = AgentRegistry(data_file="data/test_collab_summary6.json")
    registry.register(_make_profile("qa_collab_a"))

    analytics = PerformanceAnalytics(data_file="data/test_collab_summary6_metrics.json")
    report = analytics.generate_report(registry=registry)

    assert "Agent Trust & Collaboration" not in report


def test_singular_collaboration_does_not_say_1_collaborations():
    print_section("7. A Single Collaboration Says '1 collaboration', Not '1 collaborations'")

    registry = AgentRegistry(data_file="data/test_collab_summary7.json")
    a = registry.register(_make_profile("qa_collab_a"))
    registry.register(_make_profile("qa_collab_b"))
    a.update_trust_with_agent("qa_collab_b", 0.1)

    analytics = PerformanceAnalytics(data_file="data/test_collab_summary7_metrics.json")
    report = analytics.generate_report(registry=registry)
    print(report)

    assert "1 collaboration)" in report
    assert "1 collaborations)" not in report


def test_cli_and_dashboard_render_the_new_field():
    """Pins the two consumers added alongside the computation, the same way
    checkpoint 68's expense_breakdown test does for its own field."""
    print_section("8. Both generate_report() And The Dashboard Render collaboration_summary")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    performance_source = open(os.path.join(repo_root, "performance.py")).read()
    assert "get_collaboration_summary" in performance_source
    assert "Agent Trust & Collaboration" in performance_source

    web_server_source = open(os.path.join(repo_root, "web_server.py")).read()
    assert '"collaboration"' in web_server_source

    dashboard_source = open(os.path.join(repo_root, "static", "dashboard.html")).read()
    assert "report.collaboration" in dashboard_source
    assert "escapeHtml(rel.from_agent)" in dashboard_source, \
        "agent id must be escaped like every other dynamic field this dashboard renders"


def main():
    print("\n" + "=" * 60)
    print("  COLLABORATION_SUMMARY DEAD-FIELD REGRESSION TEST")
    print("=" * 60)

    test_collaboration_summary_empty_with_no_relationships()
    test_collaboration_summary_reads_real_recorded_history()
    test_collaboration_summary_is_directional_not_a_deduplicated_pair()
    test_get_collaboration_summary_sorts_by_collaboration_count()
    test_generate_report_prints_trust_and_collaboration_with_real_data()
    test_generate_report_omits_section_with_no_relationships()
    test_singular_collaboration_does_not_say_1_collaborations()
    test_cli_and_dashboard_render_the_new_field()

    print("\n" + "=" * 60)
    print("  [OK] All collaboration_summary tests passed!")
    print("=" * 60 + "\n")

    import pathlib
    import glob
    for f in glob.glob("data/test_collab_summary*.json"):
        pathlib.Path(f).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
