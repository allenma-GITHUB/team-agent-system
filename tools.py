"""
Tools - what an agent can actually DO, as opposed to describe.

Every agent in this system has been a single stateless completion: one
prompt in, one block of prose out, nothing in the world touched. This is
the read-only half of giving agents hands - tools an agent can call
mid-reasoning to look up real system state ("what is this department's
budget?", "what's already queued?") instead of inventing an answer and
being scored 4.0 for it either way.

READ-ONLY IS ENFORCED HERE, NOT LEFT TO CONVENTION. ToolRegistry refuses
to register anything declaring mutates=True unless explicitly constructed
with allow_mutating=True. The blast radius of a bug in the loop driving
these tools is therefore exactly zero: an agent can be wrong, but it
cannot be destructive. Write tools arrive only once they route through
the approval/escalation machinery that already exists in
agent_decisions.py and budgets.py - that machinery currently gates
imaginary spend, and side effects are the hazard it should have been
gating all along.

DELIBERATELY ABSENT: a generic read_file tool. "Read-only" is not the
same as "safe" - an agent that can read any path can read .env, and the
contents would land in an LLM prompt and then be persisted verbatim into
tasks.json. These tools read this system's own business state and
nothing else.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from budgets import budget_manager as _default_budget_manager, capacity_manager as _default_capacity_manager
from departments import DepartmentManager

# An agent asking for "every task ever" would blow out the prompt (and the
# bill) on a system that has been running a while. Tools that list things
# cap what they return and say so in the payload, rather than silently
# truncating and letting the model reason over a partial list it thinks is
# complete.
MAX_LISTED_TASKS = 25


@dataclass
class Tool:
    """One callable capability offered to an agent.

    `parameters` is a JSON Schema object, the format every provider's
    tool API is a thin re-skin of - llm_provider translates it per
    provider rather than each tool knowing about any of them.
    """
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]
    mutates: bool = False


@dataclass
class ToolResult:
    """Outcome of one tool invocation.

    Never raises out of execute(): a model WILL hallucinate tool names and
    malformed arguments, so those are ordinary control flow, not
    exceptions. The loop feeds `content` back to the model either way -
    an error the model can read and correct is worth far more than a
    traceback that kills the run.
    """
    ok: bool
    content: str
    error: Optional[str] = None


class ToolRegistry:
    """Holds the tools an agent may call, and dispatches calls to them."""

    def __init__(self, allow_mutating: bool = False):
        self._tools: Dict[str, Tool] = {}
        self.allow_mutating = allow_mutating

    def register(self, tool: Tool) -> None:
        if tool.mutates and not self.allow_mutating:
            raise ValueError(
                f"Tool '{tool.name}' declares mutates=True but this registry is "
                f"read-only. Side-effecting tools must route through the approval "
                f"machinery before being registered."
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def schemas(self) -> List[Dict[str, Any]]:
        """Provider-neutral tool declarations, sorted for stable prompts.

        Stable ordering matters beyond tidiness: it keeps the prompt
        prefix identical between calls, which is what makes provider-side
        prompt caching actually hit.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in (self._tools[n] for n in self.names())
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Run one tool call. Returns a ToolResult for every outcome,
        including unknown tools and bad arguments - see ToolResult."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                ok=False,
                content=f"No such tool: '{name}'. Available tools: {', '.join(self.names())}",
                error="unknown_tool",
            )

        if tool.mutates and not self.allow_mutating:
            # Belt and braces: register() already refuses these, but a tool
            # mutated after registration must not slip through silently.
            return ToolResult(
                ok=False,
                content=f"Tool '{name}' is not permitted: this registry is read-only.",
                error="mutating_tool_blocked",
            )

        if not isinstance(arguments, dict):
            return ToolResult(
                ok=False,
                content=f"Arguments for '{name}' must be an object, got {type(arguments).__name__}.",
                error="bad_arguments",
            )

        missing = [key for key in tool.parameters.get("required", []) if key not in arguments]
        if missing:
            return ToolResult(
                ok=False,
                content=f"Missing required argument(s) for '{name}': {', '.join(missing)}.",
                error="missing_arguments",
            )

        unexpected = [key for key in arguments if key not in tool.parameters.get("properties", {})]
        if unexpected:
            return ToolResult(
                ok=False,
                content=(f"Unexpected argument(s) for '{name}': {', '.join(unexpected)}. "
                         f"Accepted: {', '.join(tool.parameters.get('properties', {})) or 'none'}."),
                error="unexpected_arguments",
            )

        try:
            result = tool.handler(**arguments)
        except Exception as exc:  # noqa: BLE001 - a tool failing is data for the model, not a crash
            return ToolResult(
                ok=False,
                content=f"Tool '{name}' failed: {type(exc).__name__}: {exc}",
                error="handler_error",
            )

        if not isinstance(result, str):
            result = json.dumps(result, indent=2, default=str)
        return ToolResult(ok=True, content=result)


def build_readonly_registry(budgets=None, capacity=None,
                             tasks_file: str = "data/tasks.json") -> ToolRegistry:
    """The default read-only toolset: this system's own business state.

    Dependencies are injectable for the same reason everything else in
    this codebase is - so a test can hand in isolated managers instead of
    touching the shared data/ files.
    """
    budgets = budgets or _default_budget_manager
    capacity = capacity or _default_capacity_manager
    tasks_path = Path(tasks_file)

    def get_department_budget(department: str) -> Dict[str, Any]:
        budget = budgets.get_budget(department)
        if not budget:
            return {"department": department, "allocated": None,
                    "note": "No budget has been allocated for this department yet."}
        return {
            "department": department,
            "allocated": budget.allocated,
            "spent": budget.spent,
            "reserved": budget.reserved_total(),
            "available": budget.available(),
            "utilization_pct": round(budget.utilization_pct() * 100, 1),
        }

    def get_department_capacity(department: str) -> Dict[str, Any]:
        snapshot = capacity.snapshot(department)
        return {
            "department": department,
            "agent_count": snapshot.agent_count,
            "total_capacity": snapshot.total_capacity,
            "current_workload": snapshot.current_workload,
            "slack": snapshot.slack(),
            "utilization_pct": round(snapshot.utilization_pct() * 100, 1),
        }

    def list_departments() -> Dict[str, Any]:
        return {"departments": DepartmentManager.get_departments()}

    def list_tasks(status: Optional[str] = None) -> Dict[str, Any]:
        if not tasks_path.exists():
            return {"tasks": [], "note": "No tasks have been submitted yet."}
        try:
            all_tasks = json.loads(tasks_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return {"tasks": [], "error": f"Could not read task history: {exc}"}

        if status:
            all_tasks = [t for t in all_tasks if t.get("status") == status]

        shown = all_tasks[-MAX_LISTED_TASKS:]
        payload = {
            "total_matching": len(all_tasks),
            "showing": len(shown),
            "tasks": [
                {
                    "id": t.get("id"),
                    "description": t.get("description"),
                    "department": t.get("department"),
                    "status": t.get("status"),
                    "estimated_hours": t.get("estimated_hours"),
                }
                for t in shown
            ],
        }
        if len(all_tasks) > len(shown):
            payload["note"] = (f"Showing only the {len(shown)} most recent of "
                                f"{len(all_tasks)} matching tasks.")
        return payload

    registry = ToolRegistry(allow_mutating=False)

    registry.register(Tool(
        name="get_department_budget",
        description=("Look up a department's real budget: allocated, spent, reserved, "
                     "available, and utilization percentage. Use this before committing "
                     "to work rather than assuming what a department can afford."),
        parameters={
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "description": "Department name, e.g. 'engineering' or 'design'.",
                },
            },
            "required": ["department"],
        },
        handler=get_department_budget,
    ))

    registry.register(Tool(
        name="get_department_capacity",
        description=("Look up a department's real staffing capacity: how many agents it "
                     "has, how many concurrent task slots exist, how many are in use, and "
                     "how much slack remains."),
        parameters={
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "description": "Department name, e.g. 'engineering' or 'design'.",
                },
            },
            "required": ["department"],
        },
        handler=get_department_capacity,
    ))

    registry.register(Tool(
        name="list_departments",
        description="List every department this organization currently has.",
        parameters={"type": "object", "properties": {}},
        handler=list_departments,
    ))

    registry.register(Tool(
        name="list_tasks",
        description=(f"List submitted tasks, most recent {MAX_LISTED_TASKS} shown, "
                     "optionally filtered by status ('queued', 'completed', 'escalated')."),
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter: queued, completed, or escalated.",
                },
            },
        },
        handler=list_tasks,
    ))

    return registry
