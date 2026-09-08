"""
Core Framework - Event Bus, Registry, and Types
"""
import json
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
import uuid


@dataclass
class Event:
    """Represents an event in the system."""
    type: str
    timestamp: str
    data: Dict[str, Any]
    event_id: str = None

    def __post_init__(self):
        if self.event_id is None:
            self.event_id = str(uuid.uuid4())[:8]


class EventBus:
    """Publish-subscribe event system for tracing and monitoring."""

    def __init__(self, trace_dir: Path = Path("data/traces")):
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.subscribers: Dict[str, List[Callable]] = {}
        self.trace_file = self.trace_dir / f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        self.events = []

    def subscribe(self, event_type: str, callback: Callable):
        """Subscribe to an event type."""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event."""
        event = Event(
            type=event_type,
            timestamp=datetime.now().isoformat(),
            data=data
        )
        self.events.append(event)

        # Save to trace file
        with open(self.trace_file, 'a') as f:
            f.write(json.dumps(asdict(event)) + '\n')

        # Call subscribers
        for callback in self.subscribers.get(event_type, []):
            try:
                callback(event)
            except Exception as e:
                print(f"⚠ Event handler error: {e}")

    def get_traces(self, event_type: Optional[str] = None) -> List[Event]:
        """Get events, optionally filtered by type."""
        if event_type:
            return [e for e in self.events if e.type == event_type]
        return self.events

    def print_trace(self):
        """Print a summary of recorded events."""
        if not self.events:
            print("No events recorded.")
            return

        print(f"\nExecution Trace ({len(self.events)} events)")
        print("-" * 80)

        for event in self.events:
            data_str = json.dumps(event.data)[:60]
            print(f"[{event.timestamp[-8:]}] {event.type:<25} {data_str}...")


class Registry:
    """Base registry for agents and other components."""

    def __init__(self):
        self._registry: Dict[str, type] = {}

    def register(self, name: str):
        """Decorator to register a component."""
        def decorator(cls):
            self._registry[name] = cls
            cls._registry_name = name
            return cls
        return decorator

    def get(self, name: str) -> Optional[type]:
        """Get a registered component."""
        return self._registry.get(name)

    def list_registered(self) -> List[str]:
        """List all registered names."""
        return list(self._registry.keys())

    def create(self, name: str, *args, **kwargs) -> Any:
        """Create an instance of a registered component."""
        cls = self.get(name)
        if not cls:
            raise ValueError(f"Unknown component: {name}")
        return cls(*args, **kwargs)


# Global registries
agent_registry = Registry()
skill_registry = Registry()


class BaseAgent:
    """Base class for all agents."""

    agent_id: str = "base"
    supports_managed_tool_fallback: bool = False

    def __init__(self, bus: Optional[EventBus] = None, **kwargs):
        self.bus = bus or EventBus()
        self.config = kwargs

    def run(self, input: str, **kwargs) -> Dict[str, Any]:
        """Execute the agent. Override in subclasses."""
        raise NotImplementedError

    def _emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event."""
        if self.bus:
            self.bus.emit(event_type, {**data, "agent": self.agent_id})
