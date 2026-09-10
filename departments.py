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
