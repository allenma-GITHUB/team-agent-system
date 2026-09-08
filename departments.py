"""
Department Management - Routing and department definitions.
"""
import json
from pathlib import Path
from typing import List, Dict


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
