"""
Task Executor v2 - Parallel execution with event bus and agent registry
"""
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from core import EventBus, agent_registry, BaseAgent
from departments import DepartmentManager


class DepartmentHeadAgent(BaseAgent):
    """Generic LLM-backed department head. Works for any department in config.json.

    Register a class via agent_registry (e.g. @agent_registry.register("engineering_head"))
    to override this default for a specific department.
    """

    def __init__(self, department: str, llm_provider=None, **kwargs):
        super().__init__(**kwargs)
        self.department = department
        self.agent_id = f"{department}_head"
        self.llm = llm_provider

    def run(self, task: str, **kwargs) -> Dict[str, Any]:
        self._emit("agent_start", {"task": task, "department": self.department})
        start = time.time()

        prompt = (
            f"As the {self.department.title()} Head, analyze this task and provide "
            f"a brief plan with key action items:\n{task}"
        )

        analysis = f"{self.department.title()} team analyzed the task and produced a plan."
        tokens_used = 0
        provider_used = "mock"

        if self.llm:
            try:
                result = self.llm.generate(prompt, task_type=self.department)
                analysis = result.get("content", analysis)
                tokens_used = result.get("tokens_estimate", 0)
                provider_used = result.get("provider", "mock")
                self._emit("llm_response", {
                    "provider": provider_used,
                    "model": result.get("model", "Unknown"),
                    "tokens": tokens_used
                })
            except Exception as e:
                self._emit("llm_error", {"error": str(e), "provider": provider_used})

        duration = time.time() - start
        self._emit("agent_complete", {"status": "success", "duration": duration, "tokens": tokens_used})

        staff_count = DepartmentManager.get_staff_count(self.department)
        return {
            "analysis": analysis,
            "department": self.department,
            "llm_provider": provider_used,
            "tokens_used": tokens_used,
            "staff_contributions": [
                {"role": f"{self.department.title()} Staff {i + 1}", "contribution": f"Task component {i + 1}"}
                for i in range(staff_count)
            ]
        }


class TaskExecutor:
    """Executes tasks with parallel support and event tracing."""

    def __init__(self, llm_provider, bus: Optional[EventBus] = None, max_workers: int = 4):
        self.llm = llm_provider
        self.bus = bus or EventBus()
        self.max_workers = max_workers
        self.agents = {}

    def get_agent_for_department(self, department: str) -> BaseAgent:
        """Get or create an agent for a department. Uses a registered override if
        one exists (see agent_registry), otherwise the generic LLM-backed head."""
        if department not in self.agents:
            agent_name = f"{department}_head"
            if agent_registry.get(agent_name):
                self.agents[department] = agent_registry.create(agent_name, bus=self.bus, llm_provider=self.llm)
            else:
                self.agents[department] = DepartmentHeadAgent(department, bus=self.bus, llm_provider=self.llm)

        return self.agents[department]

    def execute(self, department: str, task_description: str) -> Dict[str, Any]:
        """Execute a task (single, synchronous)."""
        self.bus.emit("task_execute_start", {
            "task": task_description[:60],
            "department": department
        })

        start = time.time()
        agent = self.get_agent_for_department(department)
        result = agent.run(task_description)
        duration = time.time() - start

        self.bus.emit("task_execute_end", {
            "duration": duration,
            "department": department,
            "status": "completed"
        })

        execution_steps = result.get("staff_contributions", [])
        return {
            "department": department,
            "analysis": result.get("analysis", ""),
            "staff_count": len(execution_steps),
            "execution_steps": execution_steps,
            "summary": f"{department.title()} team completed task in {duration:.2f}s",
            "details": f"Task: {task_description[:60]}..."
        }

    def execute_parallel(self, tasks: List[tuple]) -> List[Dict[str, Any]]:
        """Execute multiple tasks in parallel.

        Args:
            tasks: List of (department, description) tuples

        Returns:
            List of results in original order
        """
        self.bus.emit("parallel_execution_start", {
            "num_tasks": len(tasks),
            "max_workers": self.max_workers
        })

        results = [None] * len(tasks)
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.execute, dept, desc): i
                for i, (dept, desc) in enumerate(tasks)
            }

            completed = 0
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                    completed += 1
                    self.bus.emit("parallel_task_completed", {
                        "completed": completed,
                        "total": len(tasks)
                    })
                except Exception as e:
                    self.bus.emit("parallel_task_error", {
                        "index": idx,
                        "error": str(e)
                    })
                    results[idx] = {"error": str(e)}

        total_duration = time.time() - start_time
        self.bus.emit("parallel_execution_end", {
            "total_duration": total_duration,
            "tasks_completed": completed
        })

        return results
