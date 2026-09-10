"""
Agent Decision Engine - Autonomous decision-making for agents.
Agents decide: should I do this? Who should do this? Can I do this now?
"""
from typing import Optional, List, Tuple
from dataclasses import dataclass
from agent_state import AgentState, agent_registry as _default_agent_registry


@dataclass
class DecisionContext:
    """Information about a task requiring a decision."""
    task_id: str
    task_type: str  # e.g., "code_review", "design", "analysis"
    required_skills: List[str]
    complexity: float  # 0-1 scale
    urgency: float  # 0-1 scale (deadline pressure)
    estimated_hours: float
    required_approval_level: int  # 1-5, where 5 requires executive approval


@dataclass
class DecisionResult:
    """Result of an agent's decision."""
    decision: str  # "execute", "delegate", "escalate", "reject"
    assigned_agent_id: Optional[str]
    reasoning: str
    confidence: float  # 0-1 scale
    approval_required: bool
    recommended_deadline: Optional[float] = None  # hours from now


class AgentDecisionEngine:
    """Decision-making logic for agents processing tasks."""

    def __init__(self, agent_state: AgentState, registry=None):
        self.agent = agent_state
        # Defaults to the shared global agent registry; tests (or a caller
        # managing an isolated set of agents) can inject their own instead.
        self.registry = registry or _default_agent_registry

    def can_execute(self, context: DecisionContext) -> Tuple[bool, str]:
        """Can this agent execute the task?"""
        # Check capability match
        skill_gaps = set(context.required_skills) - set(self.agent.profile.expertise_areas)
        if skill_gaps and self.agent.profile.skill_level < 4:
            return False, f"Missing expertise in: {', '.join(skill_gaps)}"

        # Check availability
        if not self.agent.is_available(context.estimated_hours):
            return False, "Insufficient capacity or workload limits"

        # Check constraints
        for constraint in self.agent.profile.constraints:
            if constraint == "cannot_execute_alone" and context.complexity > 0.7:
                return False, "Task too complex to execute alone"
            if constraint == "needs_approval_over_50k" and context.task_id.startswith("high_budget"):
                return False, "High-budget tasks need approval"

        return True, "Ready to execute"

    def should_execute(self, context: DecisionContext) -> bool:
        """Should this agent execute given preference and affinity?"""
        affinity = self.agent.learned_preferences.get(context.task_type, 0.0)

        # If highly preferred, execute
        if affinity > 0.7:
            return True

        # If disliked, delegate if possible
        if affinity < -0.5:
            return False

        # Neutral: decide based on workload and urgency
        if self.agent.current_workload >= self.agent.profile.max_concurrent_tasks * 0.8:
            return False  # Getting full, delegate if urgent

        return True  # Moderate preference + available = execute

    def find_best_delegate(self, context: DecisionContext) -> Optional[AgentState]:
        """Find best agent to delegate to."""
        candidates = self.registry.available_agents(
            skill_required=context.required_skills[0] if context.required_skills else None
        )

        if not candidates:
            return None

        # Rank candidates by:
        # 1. Skill level match
        # 2. Affinity for task type
        # 3. Current workload (prefer less busy)
        # 4. Trust score with delegator

        def rank_candidate(candidate: AgentState) -> float:
            skill_match = candidate.profile.skill_level / 5.0
            affinity = (candidate.learned_preferences.get(context.task_type, 0) + 1) / 2  # 0-1
            workload_factor = (candidate.profile.max_concurrent_tasks - candidate.current_workload) / candidate.profile.max_concurrent_tasks
            trust_factor = self.agent.relationships.get(candidate.profile.agent_id, None)
            trust = trust_factor.trust_score if trust_factor else 0.5

            return skill_match * 0.4 + affinity * 0.3 + workload_factor * 0.2 + trust * 0.1

        best = max(candidates, key=rank_candidate)
        return best if rank_candidate(best) > 0.5 else None

    def requires_approval(self, context: DecisionContext) -> bool:
        """Does this task require approval?"""
        if context.required_approval_level > self.agent.profile.skill_level:
            return True

        # High-complexity or high-cost decisions need approval
        if context.complexity > 0.8 or context.estimated_hours > 16:
            return True

        return False

    def decide(self, context: DecisionContext) -> DecisionResult:
        """Make a decision about the task."""

        # Check if we can execute at all
        can_do, reason = self.can_execute(context)
        if not can_do:
            # Try to delegate
            delegate = self.find_best_delegate(context)
            if delegate:
                return DecisionResult(
                    decision="delegate",
                    assigned_agent_id=delegate.profile.agent_id,
                    reasoning=f"Delegating to {delegate.profile.name}: {delegate.profile.agent_type}. (Own reason: {reason})",
                    confidence=0.8,
                    approval_required=False
                )
            else:
                return DecisionResult(
                    decision="escalate",
                    assigned_agent_id=None,
                    reasoning=f"Cannot execute ({reason}) and no suitable delegates available. Escalating.",
                    confidence=0.6,
                    approval_required=True
                )

        # We can do it - should we?
        if not self.should_execute(context):
            # Try to delegate to someone who likes it more
            delegate = self.find_best_delegate(context)
            if delegate:
                affinity_diff = delegate.learned_preferences.get(context.task_type, 0) - \
                               self.agent.learned_preferences.get(context.task_type, 0)
                return DecisionResult(
                    decision="delegate",
                    assigned_agent_id=delegate.profile.agent_id,
                    reasoning=f"Delegating to {delegate.profile.name} who has higher affinity ({affinity_diff:.1f} difference)",
                    confidence=0.75,
                    approval_required=False
                )

        # We're executing
        needs_approval = self.requires_approval(context)

        return DecisionResult(
            decision="execute" if not needs_approval else "execute",
            assigned_agent_id=self.agent.profile.agent_id,
            reasoning=f"Executing with skill level {self.agent.profile.skill_level}/5, affinity: {self.agent.learned_preferences.get(context.task_type, 0):.1f}",
            confidence=min(0.95, 0.5 + self.agent.profile.skill_level * 0.1),
            approval_required=needs_approval,
            recommended_deadline=context.estimated_hours
        )


class OrganizationDecisionMaker:
    """High-level decisions made by leadership (LeaderAgent)."""

    def __init__(self, leader_agent_id: str, registry=None):
        registry = registry or _default_agent_registry
        self.leader = registry.get(leader_agent_id)
        if not self.leader or self.leader.profile.agent_type != "LeaderAgent":
            raise ValueError(f"Agent {leader_agent_id} is not a LeaderAgent")

    def approve_decision(self, decision: DecisionResult, budget_available: float) -> bool:
        """Leader approves or rejects a decision."""
        if not decision.approval_required:
            return True

        # Check budget
        estimated_cost = decision.recommended_deadline * 100 if decision.recommended_deadline else 500  # mock cost
        if estimated_cost > budget_available:
            return False

        # Check strategic alignment (simplified)
        if decision.confidence < 0.6:
            return False

        return True

    def resolve_conflict(self, agent1_decision: DecisionResult, agent2_decision: DecisionResult) -> DecisionResult:
        """Resolve conflicting decisions (e.g., timeline disputes)."""
        # Merge decisions, preferring higher confidence
        if agent1_decision.confidence > agent2_decision.confidence:
            return agent1_decision
        return agent2_decision
