"""
Department Management - Routing and department definitions.
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Keyword-based effort tiers for estimate_hours(). Deliberately coarse: this
# is a starting default for --hours when a caller doesn't supply one, not a
# real estimate - an actual project would get its estimate from a human or
# an "estimation" workflow step (see workflows.py's feature_request
# template), not from scanning the task description for adjectives.
LOW_EFFORT_KEYWORDS = ["typo", "quick", "small", "minor", "tweak", "trivial", "copy change"]
HIGH_EFFORT_KEYWORDS = ["migration", "redesign", "overhaul", "rewrite", "rearchitect", "platform"]
LOW_EFFORT_HOURS = 0.5
DEFAULT_EFFORT_HOURS = 1.0
HIGH_EFFORT_HOURS = 16.0

# Complexity tiers for estimate_complexity(). Same deliberately-coarse
# keyword approach as the effort tiers above, and the same caveat: this is a
# starting signal, not an assessment.
#
# The thresholds it has to clear are not arbitrary - agent_decisions.py
# already tests complexity at two specific points, and both were unreachable
# while every task was handed a hardcoded 0.5:
#   > 0.7  the "cannot_execute_alone" constraint (config.json gives it to
#          engineering_head and design_head)
#   > 0.8  requires_approval()
# So HIGH must clear both and LOW must clear neither.
HIGH_COMPLEXITY_KEYWORDS = [
    "migration", "redesign", "overhaul", "rewrite", "rearchitect", "platform",
    "architecture", "security", "compliance", "scale", "refactor", "integration",
]
LOW_COMPLEXITY_KEYWORDS = [
    "typo", "quick", "small", "minor", "tweak", "trivial", "copy change", "rename",
]
HIGH_COMPLEXITY = 0.9
DEFAULT_COMPLEXITY = 0.5
LOW_COMPLEXITY = 0.2

# Urgency tiers for estimate_urgency(). Same coarse keyword approach as the
# complexity/effort tiers above, and the same caveat: a starting signal, not
# a real deadline. Every task used to be handed a hardcoded urgency=0.5 -
# DecisionContext.urgency was declared, populated by every caller, and
# documented in agent_decisions.py's should_execute() as a decision input,
# but nothing read it (confirmed live: a near-full agent delegated an
# identical task at urgency 0.1 and 0.9). Fixing should_execute() to read
# it needed a real, varying signal to read, not a lone constant - so these
# tiers exist for the same reason the complexity ones do. Reuses the exact
# complexity tier VALUES (0.9/0.5/0.2) for the >= 0.5 "urgent enough to
# delegate" threshold should_execute() now checks: HIGH must clear it, LOW
# must not, and DEFAULT (every task with no urgency keyword) sits exactly
# on the boundary that already matched every real task's old hardcoded
# behavior - so a task with no urgency language still delegates when full,
# exactly as before.
HIGH_URGENCY_KEYWORDS = [
    "urgent", "asap", "emergency", "critical", "immediately", "outage",
    "blocking", "right away", "hotfix", "deadline",
]
LOW_URGENCY_KEYWORDS = [
    "whenever", "no rush", "low priority", "someday", "eventually",
    "not urgent", "no hurry",
]
HIGH_URGENCY = 0.9
DEFAULT_URGENCY = 0.5
LOW_URGENCY = 0.2


def _keyword_pattern(keywords: List[str]) -> "re.Pattern":
    """Compile a \\b-bounded alternation, same approach as route_task()'s
    inline re.search() and _SKILL_KEYWORD_PATTERN below.

    estimate_hours()/estimate_complexity()/estimate_urgency() used to check
    these tiers with plain `kw in desc_lower` substring containment, on the
    documented claim that their keywords are "long enough that an accidental
    substring hit is implausible" - false for real English words: "typo" is
    a substring of "typography"/"typology", "migration" is a substring of
    "immigration", "critical" is a substring of "uncritical". Found live
    (checkpoint 97): "Update the typography ... marketing site" estimated
    as a 0.5h/0.2-complexity typo fix, and "Review the new immigration
    policy ..." estimated as a 16h/0.9-complexity platform migration -
    neither task mentions a typo or a migration. Reusing the word-boundary
    fix already applied to route_task()/infer_required_skills() (checkpoint
    38) closes the same bug shape here.
    """
    return re.compile(
        r"\b(" + "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True)) + r")\b"
    )


_HIGH_EFFORT_PATTERN = _keyword_pattern(HIGH_EFFORT_KEYWORDS)
_LOW_EFFORT_PATTERN = _keyword_pattern(LOW_EFFORT_KEYWORDS)
_HIGH_COMPLEXITY_PATTERN = _keyword_pattern(HIGH_COMPLEXITY_KEYWORDS)
_LOW_COMPLEXITY_PATTERN = _keyword_pattern(LOW_COMPLEXITY_KEYWORDS)
_HIGH_URGENCY_PATTERN = _keyword_pattern(HIGH_URGENCY_KEYWORDS)
_LOW_URGENCY_PATTERN = _keyword_pattern(LOW_URGENCY_KEYWORDS)

# Literal negation cues checked by _is_negated() below (checkpoint 98).
# Checkpoint 97's \b-boundary fix closed the "keyword as substring of a
# longer word" gap but left every tier blind to negation: "urgent", "critical",
# "blocking", and "migration" are real standalone words in "not urgent",
# "non-critical", "isn't blocking", and "no platform migration needed" too,
# so they still tripped HIGH_URGENCY/HIGH_EFFORT after that fix. Confirmed
# live before this change: estimate_urgency("This is not urgent, take your
# time") returned 0.9 (HIGH), not the LOW_URGENCY_KEYWORDS phrase "not
# urgent" already sitting right there in the tier list above, unreachable
# because HIGH's bare "urgent" match ran first.
#
# Deliberately narrow scope, same spirit as _keyword_pattern()'s own
# boundary fix: only literal, local negation - a cue word within the same
# clause and within _NEGATION_WINDOW_WORDS words immediately before the
# matched keyword. Double negation ("not un-urgent"), sarcasm, and negation
# that spans a clause the cue isn't in ("Not sure, but this migration is
# critical" - "not" must not suppress "migration") are explicitly out of
# scope and left as a named gap below, not silently mishandled.
_NEGATION_WORDS = {
    "not", "no", "non", "never", "without", "lacking", "lacks", "lack",
    "isn't", "isnt", "aren't", "arent", "wasn't", "wasnt", "weren't", "werent",
    "doesn't", "doesnt", "don't", "dont", "didn't", "didnt",
    "won't", "wont", "wouldn't", "wouldnt",
    "can't", "cant", "cannot", "couldn't", "couldnt",
    "shouldn't", "shouldnt",
}
_NEGATION_WINDOW_WORDS = 4
_CLAUSE_BOUNDARY_CHARS = ".,;:!?"


def _is_negated(desc_lower: str, match_start: int) -> bool:
    """True if a negation cue sits within the same clause and within the
    last _NEGATION_WINDOW_WORDS words directly before a tier keyword match.

    Clause-bounded so negation in an earlier clause doesn't leak into this
    one (e.g. "Not sure, but this migration is critical" - the comma stops
    "not" from suppressing "migration"). Word-windowed, not just
    adjacent, so "not a major overhaul" and "no platform migration needed"
    are caught, not only "not overhaul"/"no migration".
    """
    preceding = desc_lower[:match_start]
    boundary = max((preceding.rfind(c) for c in _CLAUSE_BOUNDARY_CHARS), default=-1)
    clause = preceding[boundary + 1:]
    tokens = re.findall(r"[a-z']+", clause)
    return any(t in _NEGATION_WORDS for t in tokens[-_NEGATION_WINDOW_WORDS:])


def _matches_non_negated(pattern: "re.Pattern", desc_lower: str) -> bool:
    """True if `pattern` has at least one match in desc_lower that isn't
    negated - i.e. a real, un-negated hit for that tier, not just any hit.
    """
    return any(
        not _is_negated(desc_lower, match.start())
        for match in pattern.finditer(desc_lower)
    )

# Seniority a task's approver needs, by complexity tier. Compared against the
# deciding agent's own skill_level in requires_approval(), so the same task
# can need sign-off from a skill-3 support head and not from a skill-4
# engineering head - which is the point of having the check at all.
HIGH_APPROVAL_LEVEL = 4
DEFAULT_APPROVAL_LEVEL = 2
LOW_APPROVAL_LEVEL = 1

# Task-derived skill tags for infer_required_skills(), checked against the
# deciding agent's own expertise_areas in can_execute()'s skill-gap test.
#
# Restricted deliberately to the exact vocabulary config.json's department
# heads already declare as their expertise_areas (e.g. engineering_head:
# architecture/code_review/deployment) - see checkpoint 37 in
# DAILY_PROGRESS.md. A real skill gap can therefore only be found against
# expertise that already exists in config, never invented on the fly. A
# department created via create_department() (no matching "_head" entry, so
# expertise_areas falls back to the placeholder [department]) is kept out of
# this check entirely by the caller (task_executor_v2.decide_on_task) - a
# mismatch against a placeholder would prove the placeholder is incomplete,
# not that the agent lacks a real capability. Adding a department's real
# vocabulary here (as "product_head" was, checkpoint 42) is what actually
# turns on the skill-gap check and coverage-aware delegate ranking for it -
# a config.json "agents" entry alone is necessary but not sufficient.
SKILL_KEYWORDS = {
    # engineering_head: architecture, code_review, deployment
    "architecture": "architecture", "rearchitect": "architecture",
    "code review": "code_review",
    "deploy": "deployment", "deployment": "deployment", "release": "deployment",
    # design_head: ui_design, ux_research, design_systems
    "ui": "ui_design", "interface design": "ui_design",
    "ux research": "ux_research", "user research": "ux_research", "usability": "ux_research",
    "design system": "design_systems",
    # research_head: data_analysis, research_methodology, insights
    "data analysis": "data_analysis", "analyze data": "data_analysis", "statistics": "data_analysis",
    "methodology": "research_methodology", "survey": "research_methodology",
    "insights": "insights",
    # support_head: customer_service, problem_solving, documentation
    "customer service": "customer_service", "support ticket": "customer_service",
    "troubleshoot": "problem_solving", "debug": "problem_solving",
    "documentation": "documentation", "write docs": "documentation",
    # sales_head: sales_strategy, negotiation, customer_relations
    "sales strategy": "sales_strategy", "pricing strategy": "sales_strategy",
    "negotiation": "negotiation", "negotiate": "negotiation",
    "customer relations": "customer_relations", "client relationship": "customer_relations",
    # product_head: product_strategy, roadmap_planning, requirements_gathering
    "product strategy": "product_strategy",
    "roadmap": "roadmap_planning", "prioritize": "roadmap_planning", "prioritization": "roadmap_planning",
    "requirements": "requirements_gathering", "user story": "requirements_gathering",
    "user stories": "requirements_gathering",
    # finance_head: budget_planning, financial_analysis, cost_estimation
    "budget": "budget_planning", "funding": "budget_planning",
    "financial": "financial_analysis", "finance": "financial_analysis",
    "cost": "cost_estimation", "expense": "cost_estimation", "spend": "cost_estimation",
}

# Matched with \b word boundaries, not plain substring "in": naive substring
# matching flagged "Build a thing" as requiring ui_design, because "build"
# contains "ui" - found live via the test suite (test_delegation.py's
# "Build a thing" silently stopped delegating, because that phantom
# requirement got threaded into find_best_delegate()'s candidate filter).
# (The estimate_hours()/estimate_complexity()/estimate_urgency() keyword
# tiers above had the same "long enough to be safe" claim and the same bug -
# see _keyword_pattern()'s docstring and checkpoint 97.)
_SKILL_KEYWORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(SKILL_KEYWORDS, key=len, reverse=True)) + r")\b"
)


class DepartmentManager:
    """Manages departments and task routing."""

    @staticmethod
    def load_config() -> Dict:
        """Load department configuration."""
        config_file = Path("config.json")
        if config_file.exists():
            return json.loads(config_file.read_text())
        return {
            "departments": {
                "engineering": {"keywords": ["code", "bug", "feature", "deploy"], "staff": 5},
                "design": {"keywords": ["design", "ui", "ux", "mockup"], "staff": 5},
                "support": {"keywords": ["support", "help", "issue", "troubleshoot"], "staff": 5},
                "research": {"keywords": ["research", "analyze", "data", "insights"], "staff": 5},
                "sales": {"keywords": ["sales", "customer", "pitch", "proposal"], "staff": 5},
            },
            "default_department": "engineering",
        }

    @staticmethod
    def route_task(description: str) -> str:
        """Route task to appropriate department based on description.

        Keywords match on \\b word boundaries, not plain substring "in" -
        the same fix already applied to infer_required_skills() below for
        the same reason (see SKILL_KEYWORDS's comment / checkpoint 38).
        Found live while adding the "product" department: naive substring
        matching sent "Prioritize the roadmap and gather requirements from
        stakeholders" to "design", because "requirements" contains "ui"
        ("req-UI-rements") - none of that task is UI work, and "design"'s
        real keyword list has nothing else in common with it. Any short
        keyword in any department's list (config.json is data, not code,
        so a future department could add one) is equally exposed.

        A keyword only counts if it has at least one non-negated match
        (_is_negated(), below) - the identical negation-blindness checkpoint
        98 fixed for estimate_hours()/estimate_complexity()/estimate_urgency(),
        never applied here even though this scoring loop has the exact same
        shape. Confirmed live: "This is not a sales deal, it is a support
        issue for an unhappy client" scored sales=3 ("sales"/"deal"/"client",
        all literal keyword hits, two of them inside the very clause that
        rules sales out) against support=2 ("support"/"issue") and routed
        to SALES - the one department the sentence explicitly denies.
        """
        config = DepartmentManager.load_config()
        departments = config.get("departments", {})
        desc_lower = description.lower()

        # Score each department - only a keyword with at least one
        # non-negated occurrence counts, same convention as
        # _matches_non_negated() above (that helper takes a single
        # precompiled alternation pattern for a fixed tier list; here each
        # department's keyword list comes from config.json at call time,
        # so each keyword gets its own ad hoc \b-bounded pattern instead).
        scores = {}
        for dept_name, dept_config in departments.items():
            keywords = dept_config.get("keywords", [])
            score = 0
            for kw in keywords:
                pattern = re.compile(r"\b" + re.escape(kw.lower()) + r"\b")
                if any(not _is_negated(desc_lower, match.start())
                       for match in pattern.finditer(desc_lower)):
                    score += 1
            scores[dept_name] = score

        # Return highest scored department, or default
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)

        return config.get("default_department", "engineering")

    @staticmethod
    def estimate_hours(description: str) -> float:
        """Rough default effort estimate from task description keywords.

        This exists so a task submitted without an explicit --hours isn't
        always billed/measured as a flat 1.0h regardless of what it actually
        says - "quick typo fix" and "platform migration" shouldn't cost and
        measure identically. It's a coarse starting point, always overridable,
        not a substitute for a real estimate.
        """
        desc_lower = description.lower()

        if _matches_non_negated(_HIGH_EFFORT_PATTERN, desc_lower):
            return HIGH_EFFORT_HOURS
        if _matches_non_negated(_LOW_EFFORT_PATTERN, desc_lower):
            return LOW_EFFORT_HOURS
        return DEFAULT_EFFORT_HOURS

    @staticmethod
    def estimate_complexity(description: str) -> float:
        """Rough task complexity on a 0-1 scale, from the description.

        Exists because every task was handed a hardcoded `complexity=0.5`,
        which sits below both thresholds agent_decisions.py tests it at - so
        the "cannot_execute_alone" constraint and the complexity-based
        approval trigger were unreachable for every task ever submitted. A
        platform migration and a typo fix were equally complex, forever.

        Coarse on purpose, exactly like estimate_hours(): a real assessment
        would come from a human or an estimation workflow step, not from
        scanning prose for nouns. The point is that the number now varies
        with the task at all.
        """
        desc_lower = description.lower()

        if _matches_non_negated(_HIGH_COMPLEXITY_PATTERN, desc_lower):
            return HIGH_COMPLEXITY
        if _matches_non_negated(_LOW_COMPLEXITY_PATTERN, desc_lower):
            return LOW_COMPLEXITY
        return DEFAULT_COMPLEXITY

    @staticmethod
    def estimate_urgency(description: str) -> float:
        """Rough deadline pressure on a 0-1 scale, from the description.

        Exists because every task was handed a hardcoded `urgency=0.5` -
        see the tier comment above. Coarse on purpose, exactly like
        estimate_complexity()/estimate_hours(): a real deadline would come
        from a human or a task due-date, not from scanning prose for
        adjectives. The point is that the number now varies with the task
        at all, so should_execute()'s workload-vs-urgency branch has
        something real to read.
        """
        desc_lower = description.lower()

        if _matches_non_negated(_HIGH_URGENCY_PATTERN, desc_lower):
            return HIGH_URGENCY
        if _matches_non_negated(_LOW_URGENCY_PATTERN, desc_lower):
            return LOW_URGENCY
        return DEFAULT_URGENCY

    @staticmethod
    def approval_level_for_complexity(complexity: float) -> int:
        """Seniority (1-5) a task of this complexity needs to sign it off.

        Paired with estimate_complexity() so that required_approval_level
        stops being the hardcoded 2 that no department head's skill_level
        (3-5) could ever fall below - which made that approval trigger dead
        alongside the other two.
        """
        if complexity > 0.8:
            return HIGH_APPROVAL_LEVEL
        if complexity <= 0.3:
            return LOW_APPROVAL_LEVEL
        return DEFAULT_APPROVAL_LEVEL

    @staticmethod
    def infer_required_skills(description: str) -> List[str]:
        """Task-derived skill tags, for the required_skills a decision
        context hands to can_execute()'s skill-gap check.

        Permissive by design when nothing matches: returns [] rather than
        guessing a tag, because an empty required_skills list can never
        produce a gap (set([]) - anything == set()). The alternative -
        always inferring something - is exactly what checkpoint 37 flagged
        as too broad a blast radius for one session: it would fail the gap
        check for nearly every task on a department whose config.json entry
        is the generic placeholder ([department]). Absence of a recognized
        keyword is treated as "no specific skill demand", not as license to
        assume one that isn't in the text.

        A keyword only counts if it has at least one non-negated match
        (_is_negated(), checkpoint 98) - the identical negation-blindness
        bug checkpoint 98 fixed for estimate_hours()/estimate_complexity()/
        estimate_urgency() and checkpoint 102 fixed for route_task(), never
        swept to this sibling even though it scans the same desc_lower with
        the same keyword-tier shape. Confirmed live (checkpoint 108):
        infer_required_skills("This is not a deployment issue - just a
        customer support ticket about a confusing error message") returned
        ['deployment', 'customer_service'] - "deployment" is a bare
        word-boundary hit inside the very clause denying it - and
        support_head (expertise customer_service/problem_solving/
        documentation, no "deployment") then escalated that plain support
        ticket via can_execute()'s real skill-gap check, reasoning "Missing
        expertise in: deployment", instead of executing it.
        """
        desc_lower = description.lower()
        skills = []
        for match in _SKILL_KEYWORD_PATTERN.finditer(desc_lower):
            if _is_negated(desc_lower, match.start()):
                continue
            skill = SKILL_KEYWORDS[match.group(1)]
            if skill not in skills:
                skills.append(skill)
        return skills

    @staticmethod
    def get_departments() -> List[str]:
        """Get all departments."""
        config = DepartmentManager.load_config()
        return list(config.get("departments", {}).keys())

    @staticmethod
    def get_staff_count(department: str) -> int:
        """Get staff count for department."""
        config = DepartmentManager.load_config()
        dept_config = config.get("departments", {}).get(department, {})
        return dept_config.get("staff", 5)

    @staticmethod
    def get_staff_range(department: str) -> Optional[Tuple[int, int]]:
        """The [min, max] staffing band declared on this department's own
        head config (e.g. "engineering_head": {"staff_range": [5, 15]}).

        Declared on every built-in department head since this project's
        very first commit (d894e20) and never read anywhere until now -
        get_staff_count() returned departments.json's flat `staff` number
        regardless of it. Looked up by convention (f"{department}_head",
        the same id DepartmentHeadAgent.agent_id uses - see
        get_agent_config()'s own docstring), not every department: a
        custom one created via create_department(), or one with no
        matching "agents" entry in config.json, has none - callers must
        treat None as "no declared band," not as a band of [0, 0].
        """
        agent_config = DepartmentManager.get_agent_config(f"{department}_head")
        if not agent_config or "staff_range" not in agent_config:
            return None
        low, high = agent_config["staff_range"]
        return int(low), int(high)

    @staticmethod
    def get_monthly_budget(department: str) -> float:
        """Get the configured monthly budget for a department."""
        config = DepartmentManager.load_config()
        dept_config = config.get("departments", {}).get(department, {})
        return dept_config.get("monthly_budget", 10000)

    @staticmethod
    def get_agent_config(agent_id: str) -> Optional[Dict]:
        """Get this agent's config.json definition (under "agents"), if any.

        agent_id is expected to match config.json's keys directly - e.g.
        "engineering_head", the same id DepartmentHeadAgent constructs as
        f"{department}_head". Returns None if config.json has no entry for
        this specific agent (most departments other than the five defined
        ones, or a custom department).
        """
        config = DepartmentManager.load_config()
        return config.get("agents", {}).get(agent_id)

    @staticmethod
    def create_department(name: str, keywords: List[str], staff_count: int = 5):
        """Create a new department."""
        config = DepartmentManager.load_config()
        config["departments"][name] = {
            "keywords": keywords,
            "staff": staff_count
        }
        Path("config.json").write_text(json.dumps(config, indent=2))

    @staticmethod
    def list_departments() -> str:
        """List all departments with info."""
        config = DepartmentManager.load_config()
        depts = config.get("departments", {})

        output = "\nDepartments:\n"
        for name, info in depts.items():
            keywords = ", ".join(info.get("keywords", []))
            staff = info.get("staff", 5)
            output += f"  {name.upper()}\n"
            output += f"    Staff: {staff}\n"
            output += f"    Keywords: {keywords}\n"

        return output
