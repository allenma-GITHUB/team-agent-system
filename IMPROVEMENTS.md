# Team Agent System - Improvements from OpenJarvis

> **⚠️ Historical design proposal, not current documentation.** Written
> during the v1→v2 transition; its "Current" examples describe the
> then-existing `main.py`/v1 behavior, which no longer exists in this repo.
> Most of what's proposed below has since been built (and much more besides
> - see **[README.md](README.md)** for the current system and
> **[DAILY_PROGRESS.md](DAILY_PROGRESS.md)** for what was actually
> implemented and when). Left below unedited as a historical record of the
> original rationale.

## OpenJarvis Learnings

OpenJarvis (Stanford SAIL) is a local-first AI framework that shows best practices for:

- **Agent Registry Pattern** - Modular agent types registered at runtime
- **Tool Resolution** - Dynamic tool discovery and execution
- **Skills System** - Reusable task templates agents can invoke
- **Governance Hooks** - Pre-flight checks before tool execution
- **Structured Reasoning** - THOUGHT/TOOL/INPUT/FINAL_ANSWER format
- **Event Bus** - Publish-subscribe for tracing and monitoring
- **Parallel Execution** - Concurrent tool calling
- **Prompt Registry** - Centralized prompt management

## Proposed Improvements

### 1. Agent Registry Pattern ⭐⭐⭐

**Current:** Hard-coded department execution  
**Improved:** Dynamically register agent types

```python
# agents/registry.py
@AgentRegistry.register("engineering_head")
class EngineeringHeadAgent(BaseAgent):
    agent_id = "engineering_head"
    
    def run(self, task: str):
        # Head-specific logic
```

**Benefits:**

- Add new agent types without modifying core
- Hot-reload agents
- Multiple agent variants per department

### 2. Skills System ⭐⭐⭐

**Current:** Hardcoded staff contributions  
**Improved:** Dynamic skill discovery and composition

```python
# skills/engineering/code_review.skill
name: "Code Review"
description: "Review code for bugs and quality"
tags: ["engineering", "code"]
inputs:
  code: "Source code to review"
outputs:
  findings: "List of issues"
  summary: "Overall assessment"
```

**Benefits:**

- Skills are YAML/JSON (vendor-neutral)
- Chain multiple skills in a task
- Share skills across teams
- Optimize skills with trace data

### 3. Event Bus & Tracing ⭐⭐⭐

**Current:** Silent execution  
**Improved:** Full observability

```python
# Real-time insights
bus.emit("agent_start", {"agent": "engineering", "task": task})
bus.emit("tool_executed", {"tool": "code_review", "duration": 2.3})
bus.emit("skill_invoked", {"skill": "test_generation", "tokens": 450})
```

**Benefits:**

- Monitor in real-time
- Trace every decision
- Debug failures
- Optimize based on patterns

### 4. Governance Hooks ⭐⭐

**Current:** No safety checks  
**Improved:** Pre-flight approval system

```python
# before_tool_call(tool_name, args) -> bool
def safety_check(tool_name, args):
    if tool_name == "deploy" and args.get("env") == "production":
        return ask_human_approval()  # Require approval for prod
    return True
```

**Benefits:**

- Control what agents can do
- Policy enforcement
- Audit trail
- Gradual autonomy increase

### 5. Parallel Tool Execution ⭐⭐

**Current:** Sequential processing  
**Improved:** Execute tasks in parallel

```python
# Current: tasks processed one-at-a-time
python main.py process  # 4 tasks = 4 sequential executions

# Improved: parallel execution
tasks = [task1, task2, task3, task4]
results = executor.run_parallel(tasks, max_workers=4)
```

**Benefits:**

- 3-4x faster processing
- Better resource utilization
- Handle more tasks simultaneously

### 6. Prompt Registry ⭐⭐

**Current:** Hardcoded prompts in code  
**Improved:** Centralized prompt management

```yaml
# prompts/department_head.yaml
engineering:
  analyze: |
    You are an engineering head. Break this into subtasks:
    {task}
  review: |
    Review this code for quality issues:
    {code}
```

**Benefits:**

- Easy to tweak prompts without code changes
- A/B test different prompts
- Version control prompts
- Share prompts across teams

### 7. Structured Reasoning ⭐⭐

**Current:** LLM generates free-form text  
**Improved:** Enforce THOUGHT/TOOL/FINAL_ANSWER format

```text
THOUGHT: This task requires code review and testing
TOOL: code_review
INPUT: {code_snippet}
TOOL: run_tests
INPUT: {test_suite}
FINAL_ANSWER: Code is ready. 3 tests passed.
```

**Benefits:**

- Predictable agent behavior
- Easier to parse results
- Better reasoning transparency
- Compatible with training pipelines

### 8. Task Templates ⭐⭐

**Current:** Free-form task descriptions  
**Improved:** Structured task templates

```python
# Task using template
task = TaskTemplate(
    type="code_review",
    inputs={
        "code": code_snippet,
        "language": "python",
        "focus": "performance"
    },
    department="engineering"
)
```

**Benefits:**

- Consistent task structure
- Type checking
- Better routing
- Easier metrics

### 9. Distributed Execution ⭐

**Current:** Single machine  
**Improved:** Multi-machine agent network

```python
# Agent on different machines
engineering_head = Agent("engineering", host="dev-machine-1")
design_head = Agent("design", host="dev-machine-2")
research_head = Agent("research", host="dev-machine-3")
```

**Benefits:**

- Horizontal scaling
- Isolate departments
- Better resource isolation

### 10. Cost Tracking & Analytics ⭐

**Current:** No metrics  
**Improved:** Full analytics dashboard

```python
analytics.track({
    "department": "engineering",
    "task": "Fix login bug",
    "duration": 12.5,
    "tokens_used": 2450,
    "llm_provider": "claude",
    "cost": 0.015,  # $0.015 for this task
    "success": True
})
```

## Implementation Priority

### Phase 1 (Immediate) - 2 hours

- [ ] Agent Registry Pattern
- [ ] Event Bus & Basic Tracing
- [ ] Parallel Task Execution

### Phase 2 (This week) - 4 hours

- [ ] Skills System (YAML-based)
- [ ] Prompt Registry
- [ ] Governance Hooks

### Phase 3 (Next week) - 4 hours

- [ ] Structured Reasoning Format
- [ ] Task Templates
- [ ] Cost Tracking & Analytics

### Phase 4 (Future) - 8+ hours

- [ ] Distributed Execution
- [ ] Web Dashboard
- [ ] Training Pipeline Integration
- [ ] Skill Optimization (DSPy)

## File Structure After Improvements

```text
team-agent-system/
├── main.py                 # CLI (unchanged)
├── config.json             # Configuration
│
├── agents/                 # NEW: Agent types
│   ├── registry.py        # Agent registry
│   ├── base.py            # BaseAgent class
│   ├── engineering.py     # EngineeringHead agent
│   ├── design.py          # DesignHead agent
│   └── ...
│
├── skills/                 # NEW: Skill definitions
│   ├── manager.py         # Skill loader
│   ├── executor.py        # Skill executor
│   └── engineering/
│       ├── code_review.yaml
│       ├── test_generation.yaml
│       └── deployment.yaml
│
├── prompts/                # NEW: Centralized prompts
│   ├── department_head.yaml
│   ├── staff_executor.yaml
│   └── governance.yaml
│
├── core/                   # NEW: Core framework
│   ├── events.py          # Event bus
│   ├── types.py           # Type definitions
│   ├── registry.py        # Base registry
│   └── tracer.py          # Tracing/analytics
│
├── utils/                  # Utilities
│   ├── llm_provider.py    # (unchanged)
│   ├── departments.py     # (unchanged)
│   └── executor.py        # (updated for parallel)
│
├── data/
│   ├── tasks.json
│   ├── results/
│   ├── traces/            # NEW: Execution traces
│   └── analytics/         # NEW: Analytics data
│
└── README.md
```

## Quick Wins (Next Session)

1. **Add Event Bus** - 30 minutes
   - Emit events for every action
   - Save traces to JSON
   - Print real-time progress

2. **Parallel Execution** - 45 minutes
   - Use Python's `concurrent.futures`
   - Process 4 tasks simultaneously
   - Show speedup metrics

3. **Agent Registry** - 1 hour
   - Make agents pluggable
   - Allow custom agent types
   - Register via decorator

## Compatibility

All improvements are **backward compatible**:

- Old tasks continue to work
- Existing API unchanged
- New features are opt-in
- Gradual migration path

## Research Integration

OpenJarvis patterns enable:

- **Trace Data Collection** - Learn from execution history
- **Policy Optimization** - Improve governance rules
- **Prompt Synthesis** - Auto-generate better prompts
- **Cost Optimization** - Route to cheaper providers
- **Performance Analysis** - Which teams work best

This foundation positions your system for next-generation AI orchestration.
