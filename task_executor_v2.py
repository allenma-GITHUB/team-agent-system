"""
Task Executor v2 - Parallel execution with event bus and agent registry
Now with autonomous agent decision-making and performance tracking.
"""
from typing import Dict, Any, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import threading
import time
from core import EventBus, agent_registry, BaseAgent
from departments import DepartmentManager
from agent_state import agent_registry as _default_agent_state_registry, AgentState, AgentProfile
from agent_decisions import AgentDecisionEngine, DecisionContext
from budgets import budget_manager as _default_budget_manager, capacity_manager as _default_capacity_manager
from performance import analytics as _default_analytics
from tools import build_readonly_registry
from agent_loop import run_agent_loop


# How many hand-offs deep a single task may go before escalating. Three is
# an org-chart depth, not a technical limit: past that, "who is actually
# doing this?" stops having a useful answer, and each extra hop is another
# department's budget spent re-deciding rather than working.
MAX_DELEGATION_DEPTH = 3

# Tool-calling rounds one agent may take on a single task. Every round is a
# full round trip billed to the department, so this bounds cost, not just
# runtime. Four leaves room to look up two or three facts and then answer;
# an agent still asking for tools after that has usually misunderstood the
# task rather than nearly finished it.
AGENT_MAX_ITERATIONS = 4


@dataclass
class _DelegationRequest:
    """Internal signal from _run_locked() to run(): "delegate this, once
    you've let go of my lock." Never leaves this module and is never a
    task result - run() always converts it into one."""
    agent_id: str
    reasoning: str


class DepartmentHeadAgent(BaseAgent):
    """Autonomous agent head with decision-making, performance tracking, and learning.

    Now uses AgentState for identity, capabilities, metrics. Makes autonomous decisions
    about task execution, delegation, or escalation.
    """

    def __init__(self, department: str, agent_state: Optional[AgentState] = None,
                 llm_provider=None, cost_per_hour: Optional[float] = None,
                 agent_state_registry=None, budget_manager=None,
                 capacity_manager=None, analytics=None,
                 delegate_resolver: Optional[Callable[[str], Any]] = None,
                 tool_registry=None, **kwargs):
        super().__init__(**kwargs)
        self.department = department
        self.agent_id = f"{department}_head"
        self.llm = llm_provider
        # Maps an agent_id to a runnable agent, so this agent can hand work
        # to a sibling. Supplied by TaskExecutor, which owns the per-department
        # agent cache; None for a standalone agent, which then executes
        # delegated-away work itself and says so rather than pretending.
        self._delegate_resolver = delegate_resolver

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

        # Read-only tools this agent may call while working. Built from this
        # agent's own managers, so an agent constructed with isolated budget
        # and capacity managers looks up those and not the global singletons.
        self._tool_registry = tool_registry or build_readonly_registry(
            budgets=self.budget_manager, capacity=self.capacity_manager)

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
        # Complexity and the approval level it implies are now read from the
        # task itself. Both were hardcoded (0.5 and 2), and both constants sat
        # on the safe side of every threshold agent_decisions.py tests them
        # against - so "cannot_execute_alone" and two of requires_approval()'s
        # three triggers could never fire for any task. Approval was reachable
        # only via estimated_hours > 16, a number typed at the CLI.
        complexity = DepartmentManager.estimate_complexity(task)
        approval_level = DepartmentManager.approval_level_for_complexity(complexity)

        # required_skills now comes from the task text itself
        # (DepartmentManager.infer_required_skills), not from this agent's
        # own expertise_areas. The previous version copied the deciding
        # agent's own profile as the task's requirement, so can_execute()'s
        # `set(required_skills) - set(expertise_areas)` was mathematically
        # always empty - confirmed live: support_head (expertise
        # customer_service/problem_solving/documentation, skill_level 3)
        # executed "Redesign the platform architecture and negotiate a new
        # sales contract pricing strategy" without a flicker, because its
        # own expertise list was standing in as the task's requirement.
        #
        # Only inferred for agents config.json actually declares expertise
        # for (the five built-in department heads). A department without a
        # config.json "agents" entry falls back to expertise_areas =
        # [department] - a placeholder, not a real skill list - so a
        # mismatch against it would prove the placeholder is incomplete,
        # not that the agent lacks a real capability. Confirmed live: with
        # inference unconditional, "Investigate a minor UI glitch" against
        # a placeholder test department escalated on a manufactured
        # "ui_design" gap that had nothing to do with the actual agent.
        # infer_required_skills() is also permissive when nothing matches
        # ([], never a gap), so a description with no recognized vocabulary
        # still can't manufacture a requirement out of nothing.
        has_declared_expertise = DepartmentManager.get_agent_config(self.agent_id) is not None
        required_skills = (
            DepartmentManager.infer_required_skills(task) if has_declared_expertise else []
        )
        decision_context = DecisionContext(
            task_id=f"task_{int(time.time())}",
            task_type=self.department,
            required_skills=required_skills,
            complexity=complexity,
            urgency=0.5,
            estimated_hours=estimated_hours,
            required_approval_level=approval_level
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

    def run(self, task: str, estimated_hours: float = 1.0,
            delegation_chain: Optional[tuple] = None, allow_delegation: bool = True,
            **kwargs) -> Dict[str, Any]:
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
        correct rather than merely fast.

        DELEGATION HAPPENS OUTSIDE THE LOCK, DELIBERATELY. _run_locked()
        never calls another agent; when it decides to delegate it returns a
        _DelegationRequest and this method dispatches it only after the
        `with` block has released. A thread therefore never holds two
        agents' locks at once, which is what makes an A->B->A delegation
        cycle impossible to deadlock: the alternative (delegating inline)
        is a textbook lock-ordering deadlock that would hang a pool worker
        permanently rather than raising anything you could debug.
        """
        chain = tuple(delegation_chain or ())

        with self._lock:
            outcome = self._run_locked(task, estimated_hours, chain=chain,
                                       allow_delegation=allow_delegation, **kwargs)

        if isinstance(outcome, _DelegationRequest):
            return self._dispatch_delegation(outcome, task, estimated_hours, chain)
        return outcome

    def _dispatch_delegation(self, request: "_DelegationRequest", task: str,
                             estimated_hours: float, chain: tuple) -> Dict[str, Any]:
        """Hand a task to another department's agent. Called only with this
        agent's lock already released (see run()).

        Every guard below ends in one of two honest outcomes - execute here
        and say the delegation didn't happen, or escalate - never in a
        silent fallback that lets the trace keep claiming the work went
        somewhere it didn't. That silent-fallback shape is precisely the
        bug this method exists to fix.
        """
        target_id = request.agent_id
        new_chain = chain + (self.agent_id,)

        def execute_here(note: str) -> Dict[str, Any]:
            self._emit("delegation_declined", {"intended_target": target_id, "reason": note})
            result = self.run(task, estimated_hours, delegation_chain=chain,
                              allow_delegation=False)
            result["delegation_declined"] = note
            result["intended_delegate"] = target_id
            return result

        def escalate(reason: str) -> Dict[str, Any]:
            self._emit("task_escalated", {"reasoning": reason})
            return {
                "analysis": f"Escalated, not executed: {reason}",
                "department": self.department,
                "agent_id": self.agent_id,
                "status": "escalated",
                "approved": False,
                "delegation_chain": list(new_chain),
            }

        # A cycle would otherwise recurse until the budget ran dry. Escalating
        # is the honest end state: every agent in the loop has already said it
        # does not want to do this task.
        if target_id in new_chain:
            return escalate(f"Delegation cycle detected: {' -> '.join(new_chain + (target_id,))}")

        if len(new_chain) >= MAX_DELEGATION_DEPTH:
            return escalate(f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) reached via "
                            f"{' -> '.join(new_chain)}")

        # No resolver: this agent was constructed standalone rather than by a
        # TaskExecutor, so there is no way to reach a sibling. Doing the work
        # and flagging it beats escalating a task that would previously have
        # completed - but it is recorded, not swallowed.
        if not self._delegate_resolver:
            return execute_here("no delegation channel available on this agent")

        target = self._delegate_resolver(target_id)
        if target is None:
            return execute_here(f"could not resolve delegate '{target_id}' to a runnable agent")

        # Same department resolves to the same cached object and therefore the
        # same lock. Self-delegation is already excluded when ranking
        # candidates; this is the belt-and-braces check that keeps a future
        # caller from reintroducing the deadlock.
        if target is self or getattr(target, "department", None) == self.department:
            return execute_here(f"delegate '{target_id}' belongs to this same department")

        self._emit("task_delegated", {
            "to_agent": target_id,
            "to_department": getattr(target, "department", "unknown"),
            "reasoning": request.reasoning,
            "chain": list(new_chain),
        })

        result = target.run(task, estimated_hours, delegation_chain=new_chain)
        # The delegate's own result stands as the record of who did the work;
        # these fields record that a hand-off happened and who made it.
        #
        # setdefault, not assignment: in an A->B->C chain this runs once per
        # frame as the stack unwinds, and plain assignment would let A
        # overwrite B's entries - reporting that A handed the task to C (it
        # didn't, B did) and replacing the full three-agent chain with A's
        # shorter view of it. The innermost frame has the most complete
        # record, so the outer ones must not clobber it.
        result.setdefault("delegated_from", self.agent_id)
        result.setdefault("delegation_chain", list(new_chain))
        result.setdefault("delegation_reasoning", request.reasoning)
        return result

    def _run_locked(self, task: str, estimated_hours: float = 1.0,
                    chain: tuple = (), allow_delegation: bool = True,
                    **kwargs) -> Dict[str, Any]:
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

        # Delegation is decided here but dispatched by run(), after this
        # method's lock is released - see run()'s docstring for why holding
        # it across another agent's call deadlocks.
        #
        # This return sits BEFORE the budget charge below on purpose: an
        # agent that hands work to someone else must not be billed for it,
        # and the delegate charges its own department when it runs. Until
        # now nothing acted on this decision at all - the task fell through
        # to the budget charge and was executed here while the event trace
        # and metrics recorded a handoff that never happened.
        if allow_delegation and decision.decision == "delegate" and decision.assigned_agent_id:
            return _DelegationRequest(
                agent_id=decision.assigned_agent_id,
                reasoning=decision.reasoning,
            )

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
        if self.llm and self.llm.supports_tools(self.department):
            # Say the numbers are available, so the model looks them up rather
            # than inventing a plausible budget - inventing one is exactly what
            # it did for every task before tools existed.
            prompt += (
                "\n\nYou can look up this organization's real state with the tools "
                "provided (budgets, staffing capacity, departments, task history). "
                "Use them for any figure you rely on instead of estimating, then "
                "give your plan."
            )

        analysis = f"{self.department.title()} team analyzed the task and produced a plan."
        tokens_used = 0
        provider_used = "mock"
        quality_score = 0.0
        used_tools = False
        tool_calls_made = 0
        tool_calls_failed = 0
        # Set when the agent stopped without finishing. Checked after the
        # bookkeeping below, which must happen either way - an unfinished
        # run still consumed budget, workload and tokens.
        incomplete_reason = None

        if self.llm:
            try:
                if self.llm.supports_tools(self.department):
                    used_tools = True
                    run = run_agent_loop(
                        prompt, self.llm, self._tool_registry,
                        max_iterations=AGENT_MAX_ITERATIONS, task_type=self.department,
                    )
                    analysis = run.final_content or analysis
                    tokens_used = run.tokens_estimate
                    provider_used = run.provider
                    tool_calls_made = len(run.tool_calls)
                    tool_calls_failed = tool_calls_made - run.successful_tool_calls()

                    # quality_score is NOT a quality measurement and never has
                    # been - it was a hardcoded 4.0 for "the HTTP call didn't
                    # throw". It is now an execution-health signal: whether the
                    # agent finished, and whether the tools it reached for
                    # actually worked. That is a real, varying observable, but
                    # judging whether the ANSWER was any good still needs a
                    # verifier this system doesn't have yet.
                    if not run.completed():
                        incomplete_reason = (
                            f"agent stopped after the {run.iterations}-iteration limit "
                            f"while still requesting tools")
                        quality_score = 1.0
                    elif tool_calls_failed:
                        quality_score = 3.0
                    else:
                        quality_score = 4.0

                    self._emit("llm_response", {
                        "provider": provider_used,
                        "model": run.model,
                        "tokens": tokens_used,
                        "tool_calls": tool_calls_made,
                        "tool_calls_failed": tool_calls_failed,
                        "iterations": run.iterations,
                        "stopped_reason": run.stopped_reason,
                    })
                else:
                    # Provider can't call tools (gemini/groq/nvidia here). Degrade
                    # to the original single completion rather than failing the
                    # task - and record that tools were not used, so a report
                    # never implies a lookup that never happened.
                    result = self.llm.generate(prompt, task_type=self.department)
                    analysis = result.get("content", analysis)
                    tokens_used = result.get("tokens_estimate", 0)
                    provider_used = result.get("provider", "mock")
                    quality_score = 4.0
                    self._emit("llm_response", {
                        "provider": provider_used,
                        "model": result.get("model", "Unknown"),
                        "tokens": tokens_used,
                        "tool_calls": 0,
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
            "status": "incomplete" if incomplete_reason else "success",
            "duration": duration,
            "tokens": tokens_used,
            "quality": quality_score,
            "workload": self.agent_state.current_workload
        })

        staff_count = DepartmentManager.get_staff_count(self.department)

        # Clean up workload
        self.agent_state.remove_task()
        self.agent_state_registry.save()

        # An agent that ran out of iterations mid-thought has NOT done the
        # task, and must never be recorded as having done it - that is the
        # exact defect fixed in checkpoints 27 and 28, and the loop's own
        # stopped_reason exists so this layer can tell the difference.
        #
        # The budget charge above is deliberately NOT refunded: the attempt
        # really did consume tokens and staff time. Reporting the spend
        # while reporting the task as unfinished is the accurate pair.
        if incomplete_reason:
            self._emit("task_escalated", {"reasoning": incomplete_reason})
            return {
                "analysis": f"Escalated, not executed: {incomplete_reason}. "
                            f"Partial output: {analysis[:200]}",
                "department": self.department,
                "agent_id": self.agent_id,
                "status": "escalated",
                "approved": False,
                "llm_provider": provider_used,
                "tokens_used": tokens_used,
                "quality_score": quality_score,
                "used_tools": used_tools,
                "tool_calls": tool_calls_made,
                "tool_calls_failed": tool_calls_failed,
            }

        return {
            "analysis": analysis,
            "department": self.department,
            "agent_id": self.agent_id,
            "status": "completed",
            "approved": True,
            "llm_provider": provider_used,
            "tokens_used": tokens_used,
            "quality_score": quality_score,
            "used_tools": used_tools,
            "tool_calls": tool_calls_made,
            "tool_calls_failed": tool_calls_failed,
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
                            analytics=self.analytics,
                            delegate_resolver=self._resolve_delegate
                        )

        return self.agents[department]

    def _resolve_delegate(self, agent_id: str) -> Optional[BaseAgent]:
        """Map a delegate's agent_id to the agent that can actually run it.

        The decision engine ranks AgentStates from the registry, which
        includes agents that are not department heads and so have no
        executor of their own (e.g. "eng_lead"). Work is therefore routed
        to the head of that agent's department - the one object per
        department that TaskExecutor caches, and so the one whose lock
        genuinely serializes that department's shared state.
        """
        registry = self.agent_state_registry or _default_agent_state_registry
        state = registry.get(agent_id)
        if not state:
            return None
        return self.get_agent_for_department(state.profile.department)

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
        status = result.get("status", "completed")

        self.bus.emit("task_execute_end", {
            "duration": duration,
            "department": department,
            "status": status
        })

        execution_steps = result.get("staff_contributions", [])
        summary = (f"{department.title()} team completed task in {duration:.2f}s"
                   if status != "escalated"
                   else f"{department.title()} team escalated task after {duration:.2f}s: {result.get('analysis', '')}")
        return {
            "department": department,
            "analysis": result.get("analysis", ""),
            "status": status,
            "approved": result.get("approved", True),
            "staff_count": len(execution_steps),
            "execution_steps": execution_steps,
            "summary": summary,
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
