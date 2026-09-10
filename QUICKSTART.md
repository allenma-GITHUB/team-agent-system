# Quick Start Guide

## 30-Second Setup

Your **Team Agent System** is ready to run immediately. No installation needed.

### 1. Submit a Task

```bash
python main_v2.py submit "Your task description here"
```

Examples:

```bash
# Engineering
python main_v2.py submit "Fix the login button not responding"

# Design
python main_v2.py submit "Create mockups for the new checkout flow"

# Research
python main_v2.py submit "Analyze competitor pricing strategies"

# Support
python main_v2.py submit "Customer can't download reports"

# Sales
python main_v2.py submit "Prepare proposal for Enterprise customer"
```

A task's effort estimate (billed hours) is guessed from its wording (e.g.
"quick"/"typo" → short, "migration"/"redesign" → long) unless you override it
with `--hours N`.

### 2. Process Tasks

```bash
python main_v2.py process
```

Your departments will automatically execute and return results - drawing
real budget and checking real staffing capacity as they go.

### 3. View Results

```bash
# List all tasks
python main_v2.py list

# Show task details
python main_v2.py show <task_id>

# System status (tasks, budget, capacity per department)
python main_v2.py status

# Combined quality/cost/budget/capacity report
python main_v2.py report
```

## That's It

Your system is running in **mock mode** (works without any API keys). See [README.md](README.md) for LLM integration and the full command reference (multi-step workflows, budget reallocation, etc.).

## Optional: Add LLM API Keys

To upgrade from mock mode to real LLM responses:

1. Copy `.env.example` to `.env`
2. Add your API keys (all optional)
3. Restart the system

Supported providers:

- Anthropic Claude
- OpenAI GPT
- Google Gemini
- Groq
- Nvidia

The system automatically detects available APIs and uses the best one.

## Common Tasks

### Create an Engineering Task

```bash
python main_v2.py submit "Database migration for user table"
```

### Create a Design Task

```bash
python main_v2.py submit "Redesign landing page with new brand guidelines"
```

### See All Pending Work

```bash
python main_v2.py list --status queued
```

### See Completed Work

```bash
python main_v2.py list --status completed
```

## System Architecture

```text
Your CLI
   ↓
Task Router (auto-detects department)
   ↓
Department Head Agent
   - checks staffing capacity
   - decides execute / delegate / escalate
   - checks & spends department budget (billed at its own seniority-based rate)
   ↓
Result (recorded to task history + org-wide analytics)
```

## Departments (Auto-Routing)

| Keywords | Department |
| ---------- | ----------- |
| code, bug, feature, deploy | Engineering |
| design, ui, ux, mockup | Design |
| support, help, issue | Support |
| research, analyze, data | Research |
| sales, customer, proposal | Sales |

Just describe your task naturally—the system routes it automatically.

## Files

- **main_v2.py** - CLI entry point
- **llm_provider.py** - LLM routing (Anthropic, OpenAI, etc.)
- **task_executor_v2.py** - Department head agents & task execution
- **agent_state.py** - Agent identity, compensation, and learning
- **budgets.py** - Department budgets and staffing capacity
- **workflows.py** - Multi-step, budget-gated approval workflows
- **strategy.py** - Cross-department budget reallocation
- **performance.py** - Quality/cost/budget/capacity analytics
- **departments.py** - Department configuration and routing
- **config.json** - Department and agent settings
- **.env** - API keys (create from .env.example)
- **data/tasks.json** - Task queue & results
- **README.md** - Full documentation

## Need Help?

See [README.md](README.md) for:

- Full command reference (including `workflow` and `strategy`)
- Configuration customization
- How to add new departments and agent profiles
- LLM provider setup
- Troubleshooting
