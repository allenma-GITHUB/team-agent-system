"""
Department Management - Routing and department definitions.
"""
import json
from pathlib import Path
from typing import List, Dict, Optional

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

# Seniority a task's approver needs, by complexity tier. Compared against the
# deciding agent's own skill_level in requires_approval(), so the same task
# can need sign-off from a skill-3 support head and not from a skill-4
# engineering head - which is the point of having the check at all.
HIGH_APPROVAL_LEVEL = 4
DEFAULT_APPROVAL_LEVEL = 2
LOW_APPROVAL_LEVEL = 1


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
        """Route task to appropriate department based on description."""
        config = DepartmentManager.load_config()
        departments = config.get("departments", {})
        desc_lower = description.lower()

        # Score each department
        scores = {}
        for dept_name, dept_config in departments.items():
            keywords = dept_config.get("keywords", [])
            score = sum(1 for kw in keywords if kw.lower() in desc_lower)
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

        if any(kw in desc_lower for kw in HIGH_EFFORT_KEYWORDS):
            return HIGH_EFFORT_HOURS
        if any(kw in desc_lower for kw in LOW_EFFORT_KEYWORDS):
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

        if any(kw in desc_lower for kw in HIGH_COMPLEXITY_KEYWORDS):
            return HIGH_COMPLEXITY
        if any(kw in desc_lower for kw in LOW_COMPLEXITY_KEYWORDS):
            return LOW_COMPLEXITY
        return DEFAULT_COMPLEXITY

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
