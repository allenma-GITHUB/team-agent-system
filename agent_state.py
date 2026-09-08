"""
Agent State & Profile - Tracks agent identity, performance, and learned preferences.
Enables autonomous decision-making based on past experience and capabilities.
"""
import json
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path


@dataclass
class AgentProfile:
    """Agent identity and expertise configuration."""
    agent_id: str
    name: str
    agent_type: str  # LeaderAgent, ManagerAgent, SpecialistAgent, CoordinatorAgent
    department: str
    expertise_areas: List[str]
    skill_level: int  # 1-5, where 5 is expert
    capabilities: List[str]
    constraints: List[str]
    max_concurrent_tasks: int = 3
    max_hours_per_week: float = 40.0


@dataclass
class PerformanceMetrics:
    """Track agent performance over time."""
    tasks_completed: int = 0
    avg_quality_score: float = 0.0  # 1-5 scale
    avg_completion_time_hours: float = 0.0
    avg_cost_per_task: float = 0.0
    error_rate: float = 0.0  # 0-1 scale
    customer_satisfaction: float = 0.0  # 1-5 scale

    def update(self, quality: float, hours: float, cost: float, success: bool = True):
        """Update metrics with new task result."""
        self.tasks_completed += 1
        self.avg_quality_score = (self.avg_quality_score + quality) / 2
        self.avg_completion_time_hours = (self.avg_completion_time_hours + hours) / 2
        self.avg_cost_per_task = (self.avg_cost_per_task + cost) / 2
        if not success:
            self.error_rate = (self.error_rate + 1) / self.tasks_completed


@dataclass
class RelationshipScore:
    """Trust and collaboration history with other agents."""
    agent_id: str
    trust_score: float = 0.5  # 0-1 scale
    collaboration_count: int = 0
    last_collaboration: Optional[str] = None  # ISO timestamp


@dataclass
class AgentState:
    """Complete state of an agent including profile, metrics, relationships."""
    profile: AgentProfile
    metrics: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    relationships: Dict[str, RelationshipScore] = field(default_factory=dict)
    current_workload: int = 0  # number of active tasks
    learned_preferences: Dict[str, float] = field(default_factory=dict)  # task_type -> affinity
    availability_until: Optional[str] = None  # ISO timestamp when current tasks complete

    def is_available(self, task_hours: float) -> bool:
        """Check if agent has capacity for new task."""
        if self.current_workload >= self.profile.max_concurrent_tasks:
            return False
        if self.profile.max_hours_per_week <= 0:
            return False
        return True

    def add_task(self):
        """Increment current workload."""
        self.current_workload = min(self.current_workload + 1, self.profile.max_concurrent_tasks)

    def remove_task(self):
        """Decrement current workload."""
        self.current_workload = max(self.current_workload - 1, 0)

    def record_performance(self, quality: float, hours: float, cost: float, success: bool = True):
        """Record task result in metrics."""
        self.metrics.update(quality, hours, cost, success)

    def update_trust_with_agent(self, other_agent_id: str, delta: float):
        """Update trust score with another agent (delta: -1 to +1)."""
        if other_agent_id not in self.relationships:
            self.relationships[other_agent_id] = RelationshipScore(other_agent_id)

        rel = self.relationships[other_agent_id]
        rel.trust_score = max(0, min(1, rel.trust_score + delta))
        rel.collaboration_count += 1
        rel.last_collaboration = datetime.utcnow().isoformat()

    def learn_preference(self, task_type: str, affinity: float):
        """Record agent's affinity for task types (1=love, 0=neutral, -1=hate)."""
        self.learned_preferences[task_type] = affinity


class AgentRegistry:
    """Central registry and persistence for all agent states."""

    def __init__(self, data_file: str = "data/agent_states.json"):
        self.data_file = Path(data_file)
        self.agents: Dict[str, AgentState] = {}
        self.load()

    def load(self):
        """Load agent states from file."""
        if self.data_file.exists():
            with open(self.data_file) as f:
                data = json.load(f)
                # Reconstruct AgentState objects from JSON
                for agent_id, agent_data in data.items():
                    profile_data = agent_data["profile"]
                    profile = AgentProfile(**profile_data)

                    metrics_data = agent_data["metrics"]
                    metrics = PerformanceMetrics(**metrics_data)

                    # Reconstruct relationships
                    relationships = {}
                    for rel_id, rel_data in agent_data.get("relationships", {}).items():
                        relationships[rel_id] = RelationshipScore(**rel_data)

                    state = AgentState(
                        profile=profile,
                        metrics=metrics,
                        relationships=relationships,
                        current_workload=agent_data.get("current_workload", 0),
                        learned_preferences=agent_data.get("learned_preferences", {}),
                        availability_until=agent_data.get("availability_until")
                    )
                    self.agents[agent_id] = state

    def save(self):
        """Persist agent states to file."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for agent_id, state in self.agents.items():
            data[agent_id] = {
                "profile": asdict(state.profile),
                "metrics": asdict(state.metrics),
                "relationships": {rel_id: asdict(rel) for rel_id, rel in state.relationships.items()},
                "current_workload": state.current_workload,
                "learned_preferences": state.learned_preferences,
                "availability_until": state.availability_until
            }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

    def register(self, profile: AgentProfile) -> AgentState:
        """Register a new agent or return existing."""
        if profile.agent_id in self.agents:
            return self.agents[profile.agent_id]

        state = AgentState(profile=profile)
        self.agents[profile.agent_id] = state
        self.save()
        return state

    def get(self, agent_id: str) -> Optional[AgentState]:
        """Retrieve agent state."""
        return self.agents.get(agent_id)

    def all(self) -> Dict[str, AgentState]:
        """Get all agents."""
        return self.agents

    def available_agents(self, skill_required: Optional[str] = None) -> List[AgentState]:
        """Get agents available for work, optionally filtered by skill."""
        available = [
            state for state in self.agents.values()
            if state.is_available(1.0)  # Simplified: check capacity
        ]

        if skill_required:
            available = [
                state for state in available
                if skill_required in state.profile.expertise_areas
            ]

        # Sort by affinity for task type (if known) and then by availability
        return sorted(available, key=lambda s: (-s.learned_preferences.get(skill_required, 0), s.current_workload))


# Global registry instance
agent_registry = AgentRegistry()
