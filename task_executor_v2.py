"""
Task Executor v2 - Parallel execution with event bus and agent registry
Now with autonomous agent decision-making and performance tracking.
"""
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from core import EventBus, agent_registry, BaseAgent
from departments import DepartmentManager
from agent_state import agent_registry as _default_agent_state_registry, AgentState, AgentProfile
from agent_decisions import AgentDecisionEngine, DecisionContext
from budgets import budget_manager as _default_budget_manager, capacity_manager as _default_capacity_manager
from performance import analytics as _default_analytics


class DepartmentHeadAgent(BaseAgent):
    """Autonomous agent head with decision-making, performance tracking, and learning.

    Now uses AgentState for identity, capabilities, metrics. Makes autonomous decisions
    about task execution, delegation, or escalation.
    """

    def __init__(self, department: str, agent_state: Optional[AgentState] = None,
                 llm_provider=None, cost_per_hour: Optional[float] = None,
                 agent_state_registry=None, budget_manager=None,
                 capacity_manager=None, analytics=None, **kwargs):
        super().__init__(**kwargs)
        self.department = department
        self.agent_id = f"{department}_head"
        self.llm = llm_provider

        # TaskExecutor caches one DepartmentHeadAgent per department and
        # reuses it across worker threads, so two tasks routed to the same
        # department can call run() on this exact object concurrently. Every
        # mutation below (workload, budget spend, performance metrics) is a
        # non-atomic read-modify-write on shared state - without this lock,
        # concurrent runs silently lose updates (confirmed: 30 concurrent
        # same-department tasks recorded as few as 28 completions and
        # correspondingly short budget spend, with zero errors reported).
        self._lock = threading.Lock()

        # Every dependency below defaults to the shared global singleton
        # (same behavior as before), but a caller - typically a test that
        # wants full isolation instead of touching data/agent_states.json,
        # data/budgets.json, etc. - can inject its own instances.
        self.agent_state_registry = agent_state_registry or _default_agent_state_registry
        self.budget_manager = budget_manager or _default_budget_manager
        self.capacity_manager = capacity_manager or _default_capacity_manager
        self.analytics = analytics or _default_analytics

        # Use provided state or load from registry
        if agent_state:
            self.agent_state = agent_state
        else:
            self.agent_state = self.agent_state_registry.get(self.agent_id)
            if not self.agent_state:
                # Prefer config.json's own definition for this specific
                # agent_id (e.g. "engineering_head": skill_level 4,
                # SpecialistAgent, real capabilities/constraints) over a
                # generic stub - config.json already describes richer
                # profiles for the five built-in department heads, but
                # nothing was reading them until now.
                agent_config = DepartmentManager.get_agent_config(self.agent_id)
                if agent_config:
                    profile = AgentProfile(
                        agent_id=self.agent_id,
                        name=agent_config.get("name", f"{department.title()} Manager"),
                        agent_type=agent_config.get("type", "ManagerAgent"),
                        department=department,
                        expertise_areas=agent_config.get("expertise_areas", [department]),
                        skill_level=agent_config.get("skill_level", 3),
                        capabilities=agent_config.get("capabilities", ["delegation", "execution"]),
                        constraints=agent_config.get("constraints", []),
                        max_concurrent_tasks=agent_config.get("max_concurrent_tasks", 3)
                    )
                else:
                    # Fallback: generic default state for a department config.json doesn't name
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
                self.agent_state = self.agent_state_registry.register(profile)

        # Compensation: derived from the agent's own seniority (agent_type,
        # skill_level) unless the caller explicitly sets a rate, instead of
        # billing a CEO and a support coordinator identically.
        self.cost_per_hour = (cost_per_hour if cost_per_hour is not None
                               else self.agent_state.profile.hourly_rate())

        # Seed a department budget from config.json on first use; leaves an
        # already-tracked period's spend alone on subsequent runs.
        self.budget_manager.ensure_allocated(department, DepartmentManager.get_monthly_budget(department))

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

        engine = AgentDecisionEngine(self.agent_state, registry=self.agent_state_registry)
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
            "context": decision_context,
            "raw_decision": decision  # agent_decisions.DecisionResult, for the budget bridge below
        }

    def run(self, task: str, estimated_hours: float = 1.0, **kwargs) -> Dict[str, Any]:
        """Serialized per-agent: TaskExecutor reuses one DepartmentHeadAgent
        per department across worker threads, and every mutation this method
        makes (workload, budget, performance metrics) is a non-atomic
        read-modify-write on state shared by every task in this department.
        Concurrent runs on the same agent without this lock silently lose
        updates - see the comment on self._lock in __init__ for the
        confirmed repro. Tasks in *different* departments still run fully
        in parallel; only same-department tasks serialize here, which is
        also the correct place to pay that cost - a real (non-mock) LLM
        call's network latency happens inside this lock too, but that's the
        same tradeoff every other resource-constrained system makes to stay
        correct rather than merely fast."""
        with self._lock:
            return self._run_locked(task, estimated_hours, **kwargs)

    def _run_locked(self, task: str, estimated_hours: float = 1.0, **kwargs) -> Dict[str, Any]:
        self._emit("agent_start", {"task": task, "department": self.department, "agent_id": self.agent_id})
        start = time.time()

        # Staffing capacity is informational for now (there's no cross-department
        # rerouting yet), but a stretched-thin department should show up in traces.
        capacity = self.capacity_manager.snapshot(self.department)
        self._emit("capacity_check", {
            "utilization": capacity.utilization_pct(),
            "agent_count": capacity.agent_count,
            "slack": capacity.slack()
        })

        decision = self.decide_on_task(task, estimated_hours=estimated_hours)["raw_decision"]

        if decision.decision == "escalate":
            self._emit("task_escalated", {"reasoning": decision.reasoning})
            return {
                "analysis": f"Escalated, not executed: {decision.reasoning}",
                "department": self.department,
                "agent_id": self.agent_id,
                "status": "escalated",
                "approved": False
            }

        # Every task draws on the department budget, not just the ones big
        # enough to need approval - otherwise routine work is free and only
        # large tasks are resource-constrained, which understates real cost.
        if decision.approval_required:
            approved, reason = self.budget_manager.approve_decision(
                decision, department=self.department, cost_per_hour=self.cost_per_hour
            )
        else:
            approved, reason = self.budget_manager.request_expense(
                department=self.department, amount=estimated_hours * self.cost_per_hour,
                category="labor", description=decision.reasoning, agent_id=self.agent_id
            )
        self._emit("budget_check", {
            "approved": approved, "reason": reason, "approval_required": decision.approval_required
        })
        if not approved:
            self._emit("task_escalated", {"reasoning": f"Budget denied: {reason}"})
            return {
                "analysis": f"Escalated, not executed: {reason}",
                "department": self.department,
                "agent_id": self.agent_id,
                "status": "escalated",
                "approved": False
            }

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

        duration = time.time() - start  # wall-clock time of this call - near-instant for a mock LLM
        mock_cost = tokens_used * 0.00001

        # Metrics track effort in business hours (the same estimated_hours the
        # budget check above priced this task at), not wall-clock duration.
        # A mock LLM call finishes in milliseconds regardless of whether the
        # task represents 1 hour or 40 - recording wall-clock time here would
        # make every duration-based metric (avg_turnaround_time, the
        # workload_rebalance/skill_gap recommendations) permanently near-zero
        # and meaningless.
        effort_hours = estimated_hours

        # Record performance - both the per-agent learning state (used for
        # delegation/preference decisions) and the org-wide analytics report
        # (performance.py), which otherwise never hears about any task run.
        self.agent_state.record_performance(
            quality=quality_score,
            hours=effort_hours,
            cost=mock_cost,
            success=quality_score >= 3.0
        )
        self.analytics.record_task(
            agent_id=self.agent_id,
            agent_name=self.agent_state.profile.name,
            department=self.department,
            quality=quality_score,
            duration=effort_hours,
            cost=mock_cost,
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
        self.agent_state_registry.save()

        return {
            "analysis": analysis,
            "department": self.department,
            "agent_id": self.agent_id,
            "status": "completed",
            "approved": True,
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

    def __init__(self, llm_provider, bus: Optional[EventBus] = None, max_workers: int = 4,
                 agent_state_registry=None, budget_manager=None,
                 capacity_manager=None, analytics=None):
        self.llm = llm_provider
        self.bus = bus or EventBus()
        self.max_workers = max_workers
        self.agents = {}
        # Guards first-creation of each department's cached agent (below) -
        # without it, two worker threads racing to process the first task
        # for a brand-new department can each see "not cached yet" and each
        # construct their own separate DepartmentHeadAgent, each with its
        # own separate lock. That defeats DepartmentHeadAgent's own
        # per-agent lock entirely, since it only protects one object at a
        # time - two objects for "the same" department don't share a lock.
        # Confirmed by repro: this raced roughly 1 in 4 runs of a
        # 15-department, 5-tasks-each concurrent load before this fix.
        self._agents_lock = threading.Lock()
        # Passed through to every DepartmentHeadAgent this executor creates -
        # None means "use the shared global singletons" (unchanged default
        # behavior); a caller can inject isolated instances for full
        # isolation (e.g. in tests, without touching data/*.json).
        self.agent_state_registry = agent_state_registry
        self.budget_manager = budget_manager
        self.capacity_manager = capacity_manager
        self.analytics = analytics

    def get_agent_for_department(self, department: str) -> BaseAgent:
        """Get or create an agent for a department. Uses a registered override if
        one exists (see agent_registry), otherwise the generic LLM-backed head.

        Double-checked locking: the common case (agent already cached) never
        touches the lock; only the rare first-creation race window does.
        """
        if department not in self.agents:
            with self._agents_lock:
                if department not in self.agents:  # re-check: another thread may have won the race
                    agent_name = f"{department}_head"
                    if agent_registry.get(agent_name):
                        self.agents[department] = agent_registry.create(agent_name, bus=self.bus, llm_provider=self.llm)
                    else:
                        self.agents[department] = DepartmentHeadAgent(
                            department, bus=self.bus, llm_provider=self.llm,
                            agent_state_registry=self.agent_state_registry,
                            budget_manager=self.budget_manager,
                            capacity_manager=self.capacity_manager,
                            analytics=self.analytics
                        )

        return self.agents[department]

    def execute(self, department: str, task_description: str, estimated_hours: float = 1.0) -> Dict[str, Any]:
        """Execute a task (single, synchronous)."""
        self.bus.emit("task_execute_start", {
            "task": task_description[:60],
            "department": department
        })

        start = time.time()
        agent = self.get_agent_for_department(department)
        result = agent.run(task_description, estimated_hours=estimated_hours)
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
            tasks: List of (department, description) or
                   (department, description, estimated_hours) tuples.
                   estimated_hours defaults to 1.0 if omitted.

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
            futures = {}
            for i, task_tuple in enumerate(tasks):
                dept, desc, *rest = task_tuple
                estimated_hours = rest[0] if rest else 1.0
                futures[executor.submit(self.execute, dept, desc, estimated_hours)] = i

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
