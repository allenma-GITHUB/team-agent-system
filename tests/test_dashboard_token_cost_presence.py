#!/usr/bin/env python3
"""
Checkpoint 111: static/dashboard.html's own rendering code - renderTaskDetail()
(the task detail panel) and loadWorkflows()'s per-step line - guarded
tokens_used/quality_score with a TRUTHY check (`if (result.tokens_used)`,
`if (result.quality_score)`) instead of a presence check, the exact same bug
checkpoint 104 fixed in web_server._task_summary() and checkpoint 106 fixed
in main_v2.py's show_task()/workflow_status(). Both of those earlier fixes
read the identical result-dict shape over the identical code path - but
dashboard.html's own JS, which reads that same shape straight off
/api/tasks/<id> and /api/workflows, was never swept to match.

Confirmed live, full stack, same repro shape as checkpoints 104/106: an
exploding tool-calling provider (task_executor_v2.py's `except Exception`
branch) leaves a real DepartmentHeadAgent.run() result with tokens_used=0
(genuinely computed - zero tokens were spent because the call failed before
producing any - not a missing key) alongside quality_score=2.0 (present,
nonzero). Running the real extracted dashboard.html source (both blocks)
under node against that exact result:

    PRE-FIX:  renderTaskDetail() extra = ['<dt>Quality Score</dt><dd>2</dd>']
              (the Tokens Used line - cost and any [NOT CHARGED] note
              included - silently missing, despite tokens_used being
              present and real)
    POST-FIX: renderTaskDetail() extra = ['<dt>Tokens Used</dt><dd>0 ($0.00000)</dd>',
                                           '<dt>Quality Score</dt><dd>2</dd>']

Fixed: both guards, at both sites, switched from truthy checks to presence
checks (`"tokens_used" in result` / `"quality_score" in result`), matching
the exact pattern already shipped at main_v2.py's two sites. The
quality_score guard is switched for consistency with that precedent even
though quality_score has no live path that leaves it at a falsy 0 today
(task_executor_v2.py sets it to 1.0/2.0/3.0/4.0 on every reachable branch -
self.llm is always a real LLMProvider instance in every production
construction site, confirmed by grep) - tokens_used is the half of this
fix actually proven live.

Same "no JS engine in the committed test suite" convention as every other
dashboard.html-only checkpoint (109, 110): this pins the fix via
structural/regex assertions against the real page source, plus a pure-Python
mirror of the exact pinned presence-check logic to exercise the three real
result shapes (never-attempted, real-zero-but-attempted, normal-nonzero) the
live node repro used.
"""

import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def _dashboard_source() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(repo_root, "static", "dashboard.html")).read()


def _detail_extra_mirror(result: dict) -> list:
    """Pure-Python mirror of the fixed renderTaskDetail() tokens_used/
    quality_score block, pinned by test_detail_block_uses_presence_checks()
    below: presence, not truthiness, decides whether each line renders."""
    extra = []
    if "tokens_used" in result:
        not_charged = result.get("token_cost_charged") is False
        charge_note = (
            f" [NOT CHARGED: {result.get('token_budget_note') or ''}]" if not_charged else ""
        )
        extra.append(
            f"Tokens Used: {result['tokens_used']} (${result.get('token_cost', 0):.5f}){charge_note}"
        )
    if "quality_score" in result:
        extra.append(f"Quality Score: {result['quality_score']}")
    return extra


def _step_line_suffix_mirror(result: dict) -> str:
    """Pure-Python mirror of the fixed loadWorkflows() step-line suffix,
    pinned by test_step_line_block_uses_presence_checks() below."""
    extra = ""
    if "quality_score" in result:
        extra += f" (quality {result['quality_score']})"
    if "tokens_used" in result:
        extra += f" (${result.get('token_cost', 0):.5f})"
    return extra


def test_detail_block_uses_presence_checks():
    """Pins the exact fixed source at the task-detail site: both guards
    must be `"field" in result`, not a truthy `result.field` check."""
    print_section("1. renderTaskDetail()'s tokens_used/quality_score Guards")

    source = _dashboard_source()
    block = re.search(
        r'if \("tokens_used" in result\) \{(.*?)if \("quality_score" in result\) \{(.*?)\n    \}',
        source, re.S,
    )
    assert block, (
        'renderTaskDetail() must guard both fields with `"field" in result` - '
        "not found, or regressed to a truthy check"
    )
    # Regression guard: the original truthy forms must not have come back.
    assert "if (result.tokens_used) {" not in source
    assert "if (result.quality_score) {" not in source


def test_step_line_block_uses_presence_checks():
    """Pins the exact fixed source at the workflow-step-line site."""
    print_section("2. loadWorkflows() Step-Line tokens_used/quality_score Guards")

    source = _dashboard_source()
    assert 'if ("quality_score" in result) extra +=' in source
    assert 'if ("tokens_used" in result) extra +=' in source
    # Regression guard: must not have regressed to the original truthy forms.
    assert "if (result.quality_score) extra +=" not in source
    assert "if (result.tokens_used) extra +=" not in source


def test_real_zero_token_cost_renders_in_task_detail():
    """The concrete bug this checkpoint fixes: an attempted-then-escalated
    task's real $0.00 cost (tokens_used=0, genuinely computed, present
    alongside a nonzero quality_score - task_executor_v2.py's except-branch
    shape) must render, not vanish as if the task were never attempted."""
    print_section("3. A Real $0 Attempted Task Renders Both Lines")

    never_attempted = {"analysis": "Escalated, not executed: Budget denied", "status": "escalated"}
    real_zero = {"tokens_used": 0, "token_cost": 0.0, "token_cost_charged": True, "quality_score": 2.0}
    normal_nonzero = {"tokens_used": 142, "token_cost": 0.00142, "token_cost_charged": True, "quality_score": 4.0}

    never_out = _detail_extra_mirror(never_attempted)
    real_zero_out = _detail_extra_mirror(real_zero)
    normal_out = _detail_extra_mirror(normal_nonzero)

    print(f"  never-attempted : {never_out}")
    print(f"  real $0 attempt : {real_zero_out}")
    print(f"  normal nonzero  : {normal_out}")

    assert never_out == [], "a task that never executed must show neither line"
    assert len(real_zero_out) == 2, (
        "pre-fix, this was 1 (Tokens Used silently dropped despite tokens_used=0 "
        "being present and real) - both lines must render post-fix"
    )
    assert "Tokens Used: 0 ($0.00000)" in real_zero_out[0]
    assert len(normal_out) == 2


def test_real_zero_token_cost_renders_in_step_line():
    """Same bug, same fix, at the workflow step-line site."""
    print_section("4. A Real $0 Step Result Renders Both Suffixes")

    step_real_zero = {"tokens_used": 0, "token_cost": 0.0, "quality_score": 2.0}
    step_no_fields = {}

    real_zero_suffix = _step_line_suffix_mirror(step_real_zero)
    no_fields_suffix = _step_line_suffix_mirror(step_no_fields)

    print(f"  real $0 step suffix : {real_zero_suffix!r}")
    print(f"  no-fields suffix    : {no_fields_suffix!r}")

    assert real_zero_suffix == " (quality 2.0) ($0.00000)"
    assert no_fields_suffix == ""


def test_live_executor_result_matches_the_pinned_shape():
    """Confirms, via the real DepartmentHeadAgent (not a hand-built dict),
    that the exploding-provider path this checkpoint's fix targets really
    does produce tokens_used=0 (present) alongside a nonzero quality_score -
    the same live confirmation checkpoints 104/106 each ran for their own
    sites, now pinned for this one too."""
    print_section("5. Live Confirmation: The Real Executor Produces This Shape")

    from agent_state import AgentProfile, AgentRegistry, PerformanceMetrics
    from budgets import BudgetManager, CapacityManager
    from performance import PerformanceAnalytics
    from task_executor_v2 import DepartmentHeadAgent

    class ExplodingToolProvider:
        def supports_tools(self, task_type="general"):
            return True

        def generate_with_tools(self, messages, tool_schemas, task_type="general"):
            raise RuntimeError("simulated provider outage")

    suffix = "dashboard_token_presence"
    registry = AgentRegistry(data_file=f"data/test_{suffix}_agents.json")
    state = registry.register(AgentProfile(
        agent_id="engineering_head", name="Engineering Head", agent_type="ManagerAgent",
        department="engineering", expertise_areas=["engineering"], skill_level=4,
        capabilities=["execution"], constraints=[], max_concurrent_tasks=3,
    ))
    state.current_workload = 0
    state.learned_preferences = {}
    state.metrics = PerformanceMetrics()

    budgets = BudgetManager(data_file=f"data/test_{suffix}_budgets.json")
    budgets.allocate("engineering", 100000)

    agent = DepartmentHeadAgent(
        "engineering", llm_provider=ExplodingToolProvider(),
        agent_state_registry=registry, budget_manager=budgets,
        capacity_manager=CapacityManager(registry=registry),
        analytics=PerformanceAnalytics(data_file=f"data/test_{suffix}_metrics.json"),
    )
    result = agent.run("Investigate a flaky build pipeline", 2.0)

    print(f"  status        : {result['status']}")
    print(f"  tokens_used    : {result['tokens_used']!r}")
    print(f"  token_cost     : {result['token_cost']!r}")
    print(f"  quality_score  : {result['quality_score']!r}")

    assert "tokens_used" in result and result["tokens_used"] == 0
    assert "token_cost" in result and result["token_cost"] == 0.0
    assert "quality_score" in result and result["quality_score"] > 0

    rendered = _detail_extra_mirror(result)
    assert len(rendered) == 2, "the dashboard's own mirrored logic must render both lines for this real result"
    assert "Tokens Used: 0" in rendered[0]


def main():
    print("\n" + "=" * 60)
    print("  DASHBOARD TOKEN COST / QUALITY SCORE PRESENCE TEST")
    print("=" * 60)

    test_detail_block_uses_presence_checks()
    test_step_line_block_uses_presence_checks()
    test_real_zero_token_cost_renders_in_task_detail()
    test_real_zero_token_cost_renders_in_step_line()
    test_live_executor_result_matches_the_pinned_shape()

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
