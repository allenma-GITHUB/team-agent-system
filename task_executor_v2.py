"""
Task Executor v2 - Parallel execution with event bus and agent registry
Now with autonomous agent decision-making and performance tracking.
"""
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from core import EventBus, agent_registry, BaseAgent
from departments import DepartmentManager
from agent_state import agent_registry as agent_state_registry, AgentState, AgentProfile
from agent_decisions import AgentDecisionEngine, DecisionContext


class DepartmentHeadAgent(BaseAgent):
    """Autonomous agent head with decision-making, performance tracking, and learning.

    Now uses AgentState for identity, capabilities, metrics. Makes autonomous decisions
    about task execution, delegation, or escalation.
    """

    def __init__(self, department: str, agent_state: Optional[AgentState] = None,
                 llm_provider=None, **kwargs):
        super().__init__(**kwargs)
        self.department = department
        self.agent_id = f"{department}_head"
        self.llm = llm_provider

        # Use provided state or load from registry
        if agent_state:
            self.agent_state = agent_state
        else:
            self.agent_state = agent_state_registry.get(self.agent_id)
            if not self.agent_state:
                # Fallback: create default state for this department
                profile = AgentProfile(
                    agent_id=self.agent_id,
                    name=f"{department.title()} Manager",
                    agent_type="ManagerAgent",
                    department=department,
                    expertise_areas=[department],
                    skill_level=3,
                    capabilities=["delegation", "execution"],
                    constraints=[]
                )
                self.agent_state = agent_state_registry.register(profile)

    def decide_on_task(self, task: str, estimated_hours: float = 1.0) -> Dict[str, Any]:
        """Use decision engine to decide how to handle the task."""
        decision_context = DecisionContext(
            task_id=f"task_{int(time.time())}",
            task_type=self.department,
            required_skills=self.agent_state.profile.expertise_areas,
            complexity=0.5,  # Simplified
            urgency=0.5,
            estimated_hours=estimated_hours,
            required_approval_level=2
        )

        engine = AgentDecisionEngine(self.agent_state)
        decision = engine.decide(decision_context)

        self._emit("agent_decision", {
            "decision": decision.decision,
            "assigned_to": decision.assigned_agent_id,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning
        })

        return {
            "decision": decision.decision,
            "assigned_agent_id": decision.assigned_agent_id,
            "requires_approval": decision.approval_required,
            "confidence": decision.confidence,
            "context": decision_context
        }

    def run(self, task: str, **kwargs) -> Dict[str, Any]:
        self._emit("agent_start", {"task": task, "department": self.department, "agent_id": self.agent_id})
        start = time.time()

        # Check workload
        self.agent_state.add_task()

        prompt = (
            f"As the {self.department.title()} Head, analyze this task and provide "
            f"a brief plan with key action items:\n{task}"
        )

        analysis = f"{self.department.title()} team analyzed the task and produced a plan."
        tokens_used = 0
        provider_used = "mock"
        quality_score = 0.0

        if self.llm:
            try:
                result = self.llm.generate(prompt, task_type=self.department)
                analysis = result.get("content", analysis)
                tokens_used = result.get("tokens_estimate", 0)
                provider_used = result.get("provider", "mock")
                quality_score = 4.0  # Mock quality score
                self._emit("llm_response", {
                    "provider": provider_used,
                    "model": result.get("model", "Unknown"),
                    "tokens": tokens_used
                })
            except Exception as e:
                self._emit("llm_error", {"error": str(e), "provider": provider_used})
                quality_score = 2.0

        duration = time.time() - start

        # Record performance
        self.agent_state.record_performance(
            quality=quality_score,
            hours=duration / 3600.0,
            cost=tokens_used * 0.00001,  # Mock cost
            success=quality_score >= 3.0
        )

        # Update preference for this task type
        self.agent_state.learn_preference(self.department, 0.3 if quality_score >= 3.0 else -0.2)

        self._emit("agent_complete", {
            "status": "success",
            "duration": duration,
            "tokens": tokens_used,
            "quality": quality_score,
            "workload": self.agent_state.current_workload
        })

        staff_count = DepartmentManager.get_staff_count(self.department)

        # Clean up workload
        self.agent_state.remove_task()
        agent_state_registry.save()

        return {
            "analysis": analysis,
            "department": self.department,
            "agent_id": self.agent_id,
            "llm_provider": provider_used,
            "tokens_used": tokens_used,
            "quality_score": quality_score,
            "metrics": {
                "avg_quality": self.agent_state.metrics.avg_quality_score,
                "tasks_completed": self.agent_state.metrics.tasks_completed,
                "error_rate": self.agent_state.metrics.error_rate
            },
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
