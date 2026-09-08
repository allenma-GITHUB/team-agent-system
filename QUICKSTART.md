# Quick Start Guide

## 30-Second Setup

Your **Team Agent System** is ready to run immediately. No installation needed.

### 1. Submit a Task

```bash
python main.py submit "Your task description here"
```

Examples:
```bash
# Engineering
python main.py submit "Fix the login button not responding"

# Design  
python main.py submit "Create mockups for the new checkout flow"

# Research
python main.py submit "Analyze competitor pricing strategies"

# Support
python main.py submit "Customer can't download reports"

# Sales
python main.py submit "Prepare proposal for Enterprise customer"
```

### 2. Process Tasks

```bash
python main.py process
```

Your departments will automatically execute and return results.

### 3. View Results

```bash
# List all tasks
python main.py list

# Show task details
python main.py show <task_id>

# System status
python main.py status
```

## That's It!

Your system is running in **mock mode** (works without any API keys). See [README.md](README.md) for LLM integration.

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
python main.py submit "Database migration for user table"
```

### Create a Design Task
```bash
python main.py submit "Redesign landing page with new brand guidelines"
```

### See All Pending Work
```bash
python main.py list --status queued
```

### See Completed Work
```bash
python main.py list --status completed
```

## System Architecture

```
Your CLI
   ↓
Task Router (auto-detects department)
   ↓
Department Head Agent (plans work)
   ↓
5 Staff Members (simulate execution)
   ↓
Results (stored locally)
```

## Departments (Auto-Routing)

| Keywords | Department |
|----------|-----------|
| code, bug, feature, deploy | Engineering |
| design, ui, ux, mockup | Design |
| support, help, issue | Support |
| research, analyze, data | Research |
| sales, customer, proposal | Sales |

Just describe your task naturally—the system routes it automatically.

## Files

- **main.py** - CLI entry point
- **llm_provider.py** - LLM routing (Anthropic, OpenAI, etc.)
- **task_executor.py** - Department heads & staff simulation
- **departments.py** - Department configuration
- **config.json** - Department settings & routing rules
- **.env** - API keys (create from .env.example)
- **data/tasks.json** - Task queue & results
- **README.md** - Full documentation

## Need Help?

See [README.md](README.md) for:
- Detailed command reference
- Configuration customization
- How to add new departments
- LLM provider setup
- Troubleshooting
