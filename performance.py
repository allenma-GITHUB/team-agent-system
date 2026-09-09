"""
Performance Tracking & Analytics - Measures and reports on agent and system metrics.
Provides data-driven insights for optimization and learning.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import json
import statistics


@dataclass
class AgentMetrics:
    """Metrics for a single agent over time."""
    agent_id: str
    name: str
    department: str

    # Performance data
    tasks_completed: int = 0
    total_duration_hours: float = 0.0
    total_cost: float = 0.0
    quality_scores: List[float] = field(default_factory=list)
    error_count: int = 0

    # Computed metrics
    avg_quality: float = 0.0
    avg_duration_hours: float = 0.0
    cost_per_task: float = 0.0
    success_rate: float = 0.0

    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def update(self, quality: float, duration: float, cost: float, success: bool):
        """Record task result."""
        self.tasks_completed += 1
        self.quality_scores.append(quality)
        self.total_duration_hours += duration
        self.total_cost += cost
        if not success:
            self.error_count += 1
        self._compute_metrics()

    def _compute_metrics(self):
        """Compute derived metrics."""
        if self.tasks_completed > 0:
            self.avg_quality = statistics.mean(self.quality_scores) if self.quality_scores else 0.0
            self.avg_duration_hours = self.total_duration_hours / self.tasks_completed
            self.cost_per_task = self.total_cost / self.tasks_completed
            self.success_rate = (self.tasks_completed - self.error_count) / self.tasks_completed

    def get_quality_trend(self, window: int = 5) -> Tuple[float, str]:
        """Get recent quality trend (last N tasks)."""
        if len(self.quality_scores) < window:
            return 0.0, "insufficient_data"

        recent = self.quality_scores[-window:]
        older = self.quality_scores[-window*2:-window] if len(self.quality_scores) >= window*2 else self.quality_scores[:window]

        recent_avg = statistics.mean(recent)
        older_avg = statistics.mean(older) if older else recent_avg

        trend = recent_avg - older_avg
        direction = "improving" if trend > 0.1 else "declining" if trend < -0.1 else "stable"

        return trend, direction


@dataclass
class DepartmentMetrics:
    """Aggregated metrics for a department."""
    department: str
    agent_ids: List[str] = field(default_factory=list)

    total_tasks: int = 0
    avg_quality: float = 0.0
    avg_cost_per_task: float = 0.0
    success_rate: float = 0.0

    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def compute_from_agents(self, agent_metrics: Dict[str, AgentMetrics]):
        """Compute department metrics from agents."""
        agents = [m for m in agent_metrics.values() if m.department == self.department]
        self.agent_ids = [a.agent_id for a in agents]

        if not agents:
            return

        self.total_tasks = sum(a.tasks_completed for a in agents)
        self.avg_quality = statistics.mean([a.avg_quality for a in agents if a.avg_quality > 0])
        self.avg_cost_per_task = statistics.mean([a.cost_per_task for a in agents if a.cost_per_task > 0])
        self.success_rate = statistics.mean([a.success_rate for a in agents if a.success_rate > 0])


@dataclass
class SystemMetrics:
    """Overall system performance metrics."""
    total_tasks: int = 0
    total_cost: float = 0.0
    avg_quality: float = 0.0
    avg_turnaround_time: float = 0.0
    system_success_rate: float = 0.0

    bottleneck_agent: Optional[str] = None
    bottleneck_department: Optional[str] = None

    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class PerformanceAnalytics:
    """Analyzes and reports on performance metrics."""

    def __init__(self, data_file: str = "data/performance_metrics.json"):
        self.data_file = Path(data_file)
        self.agent_metrics: Dict[str, AgentMetrics] = {}
        self.department_metrics: Dict[str, DepartmentMetrics] = {}
        self.system_metrics = SystemMetrics()
        self.load()

    def load(self):
        """Load metrics from file."""
        if self.data_file.exists():
            with open(self.data_file) as f:
                data = json.load(f)

                # Load agent metrics
                for agent_id, agent_data in data.get("agents", {}).items():
                    metrics = AgentMetrics(**agent_data)
                    self.agent_metrics[agent_id] = metrics

    def save(self):
        """Save metrics to file."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "agents": {aid: asdict(m) for aid, m in self.agent_metrics.items()},
            "departments": {did: asdict(m) for did, m in self.department_metrics.items()},
            "system": asdict(self.system_metrics)
        }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def record_task(self, agent_id: str, agent_name: str, department: str,
                   quality: float, duration: float, cost: float, success: bool = True):
        """Record task execution result."""
        if agent_id not in self.agent_metrics:
            self.agent_metrics[agent_id] = AgentMetrics(
                agent_id=agent_id,
                name=agent_name,
                department=department
            )

        metrics = self.agent_metrics[agent_id]
        metrics.update(quality, duration, cost, success)
        self.save()

    def get_agent_metrics(self, agent_id: str) -> Optional[AgentMetrics]:
        """Get metrics for specific agent."""
        return self.agent_metrics.get(agent_id)

    def get_department_metrics(self, department: str) -> Optional[DepartmentMetrics]:
        """Get metrics for specific department."""
        dept_metrics = DepartmentMetrics(department=department)
        dept_metrics.compute_from_agents(self.agent_metrics)
        return dept_metrics if dept_metrics.agent_ids else None

    def get_system_metrics(self) -> SystemMetrics:
        """Compute system-wide metrics."""
        if not self.agent_metrics:
            return self.system_metrics

        agents = list(self.agent_metrics.values())

        self.system_metrics.total_tasks = sum(a.tasks_completed for a in agents)
        self.system_metrics.total_cost = sum(a.total_cost for a in agents)
        self.system_metrics.avg_quality = statistics.mean([a.avg_quality for a in agents if a.avg_quality > 0])
        self.system_metrics.avg_turnaround_time = statistics.mean([a.avg_duration_hours for a in agents if a.avg_duration_hours > 0])
        self.system_metrics.system_success_rate = statistics.mean([a.success_rate for a in agents if a.success_rate > 0])

        # Find bottlenecks
        slowest_agent = max(agents, key=lambda a: a.avg_duration_hours, default=None)
        if slowest_agent:
            self.system_metrics.bottleneck_agent = slowest_agent.agent_id

        return self.system_metrics

    def get_top_performers(self, metric: str = "quality", limit: int = 5) -> List[Tuple[str, float]]:
        """Get top performing agents by metric."""
        agents = list(self.agent_metrics.values())

        if metric == "quality":
            ranked = sorted(agents, key=lambda a: a.avg_quality, reverse=True)
        elif metric == "speed":
            ranked = sorted(agents, key=lambda a: a.avg_duration_hours)
        elif metric == "cost":
            ranked = sorted(agents, key=lambda a: a.cost_per_task)
        elif metric == "success":
            ranked = sorted(agents, key=lambda a: a.success_rate, reverse=True)
        else:
            return []

        return [(a.name, getattr(a, f"avg_{metric}", 0) if metric != "speed" else a.avg_duration_hours)
                for a in ranked[:limit]]

    def get_recommendations(self) -> List[Dict[str, str]]:
        """Get AI-powered recommendations for optimization."""
        recommendations = []

        if not self.agent_metrics:
            return recommendations

        # Check for low performers
        for agent in self.agent_metrics.values():
            if agent.success_rate < 0.7:
                recommendations.append({
                    "type": "training_needed",
                    "agent": agent.name,
                    "issue": f"Success rate {agent.success_rate:.0%} below target 95%",
                    "action": f"Provide training or mentoring for {agent.name}"
                })

        # Check for overworked agents
        for agent in self.agent_metrics.values():
            if agent.avg_duration_hours > 8:
                recommendations.append({
                    "type": "workload_rebalance",
                    "agent": agent.name,
                    "issue": f"Average task duration {agent.avg_duration_hours:.1f}h exceeds capacity",
                    "action": f"Redistribute work or expand {agent.name}'s capacity"
                })

        # Check for bottlenecks
        system_avg = statistics.mean([a.avg_duration_hours for a in self.agent_metrics.values() if a.avg_duration_hours > 0])
        for agent in self.agent_metrics.values():
            if agent.avg_duration_hours > system_avg * 1.5:
                recommendations.append({
                    "type": "skill_gap",
                    "agent": agent.name,
                    "issue": f"Significantly slower than peers ({agent.avg_duration_hours:.1f}h vs {system_avg:.1f}h avg)",
                    "action": f"Assess skill gaps and provide targeted training"
                })

        return recommendations

    def generate_report(self) -> str:
        """Generate text report of all metrics."""
        report = []
        report.append("\n" + "="*60)
        report.append("SYSTEM PERFORMANCE REPORT")
        report.append("="*60)

        # System metrics
        system = self.get_system_metrics()
        report.append(f"\nSystem Overview:")
        report.append(f"  Total Tasks: {system.total_tasks}")
        report.append(f"  Avg Quality: {system.avg_quality:.1f}/5.0")
        report.append(f"  Success Rate: {system.system_success_rate:.0%}")
        report.append(f"  Avg Turnaround: {system.avg_turnaround_time:.1f}h")
        report.append(f"  Total Cost: ${system.total_cost:,.2f}")

        # Top performers
        top_quality = self.get_top_performers("quality", 3)
        if top_quality:
            report.append(f"\nTop Performers (Quality):")
            for name, score in top_quality:
                report.append(f"  - {name}: {score:.1f}/5.0")

        # Recommendations
        recs = self.get_recommendations()
        if recs:
            report.append(f"\nRecommendations:")
            for rec in recs:
                report.append(f"  - {rec['agent']}: {rec['action']}")

        report.append("\n" + "="*60 + "\n")
        return "\n".join(report)


# Global analytics instance
analytics = PerformanceAnalytics()
