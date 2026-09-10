# Team Agent System

A multi-department AI task management system that orchestrates work across departments with optional LLM routing. Runs **completely offline** without API keys, or integrates with multiple LLM providers when configured.

## Features

✅ **Works Without API Keys** - Full mock mode for immediate use  
✅ **Optional LLM Integration** - Add Anthropic, OpenAI, Gemini, Groq, Nvidia when ready  
✅ **Multi-Department Routing** - Tasks automatically routed to specialized teams  
✅ **Department Heads & Staff** - Each department has a head agent + 5 staff members  
✅ **Task Queue** - Persistent task storage with status tracking  
✅ **Graceful Degradation** - Falls back to mock if API key fails  

## Quick Start

### 1. Setup

```bash
# No dependencies required for basic operation!
# Optional: Install Python libraries for LLM support
pip install anthropic openai google-generativeai requests
```

### 2. Add API Keys (Optional)

```bash
# Copy the template
cp .env.example .env

# Edit .env and add your API keys (all are optional)
# System works fine without them
```

### 3. Run the System

```bash
# Submit a task
python main.py submit "Fix login bug"

# Process queued tasks
python main.py process

# List all tasks
python main.py list

# Show task details
python main.py show <task_id>

# System status
python main.py status
```

## Departments

The system comes with 5 default departments:

| Department | Keywords | Staff | Role |
| --- | --- | --- | --- |
| **Engineering** | code, bug, feature, deploy | 5 | Development & DevOps |
| **Design** | design, ui, ux, mockup | 5 | Visual & UX Design |
| **Support** | support, help, issue, troubleshoot | 5 | Customer Support |
| **Research** | research, analyze, data, insights | 5 | Analysis & Insights |
| **Sales** | sales, customer, pitch, proposal | 5 | Sales & Business Dev |

### Auto-Routing Example

```bash
python main.py submit "Fix the checkout button styling"  # → Design
python main.py submit "Database query is timing out"     # → Engineering
python main.py submit "Customer reported connection issue" # → Support
python main.py submit "Analyze Q3 sales trends"          # → Research
python main.py submit "Create customer proposal"          # → Sales
```

## Usage Examples

### Example 1: Bug Fix Task

```bash
$ python main.py submit "Login page shows 500 error"
✓ Task submitted: a7b2f9c1
  Department: engineering
  Description: Login page shows 500 error

$ python main.py process
Processing 1 task(s)...

[a7b2f9c1] ENGINEERING - Login page shows 500 error...
  → Result: MOCK RESPONSE: Analyzed the bug - found root cause in the database query. Fixed by adding proper indexing. Tested locally and in staging. Ready for production.

✓ All tasks processed.

$ python main.py show a7b2f9c1
Task: a7b2f9c1
Status: completed
Department: engineering
Description: Login page shows 500 error
Created: 2024-01-15T10:30:45.123456

Result:
  Summary: Engineering team completed development task...
  Details: Code reviewed, tested, and ready for production...
```

### Example 2: Design Task

```bash
$ python main.py submit "Redesign the dashboard UI"
✓ Task submitted: b8c3g0d2
  Department: design
  Description: Redesign the dashboard UI

$ python main.py process
Processing 1 task(s)...

[b8c3g0d2] DESIGN - Redesign the dashboard UI...
  → Result: MOCK RESPONSE: Created design mockups with wireframes, color palette, and component library...

✓ All tasks processed.
```

### Example 3: Research Task

```bash
$ python main.py submit "Analyze competitor pricing strategies"
✓ Task submitted: c9d4h1e3
  Department: research
  Description: Analyze competitor pricing strategies

$ python main.py process
Processing 1 task(s)...

[c9d4h1e3] RESEARCH - Analyze competitor pricing strategies...
  → Result: MOCK RESPONSE: Completed analysis of available data. Key findings: 85% positive sentiment...

✓ All tasks processed.
```

## LLM Providers

The system automatically detects available API keys and selects the best provider:

### Provider Priority

1. **Anthropic** (Claude) - Best for complex reasoning
2. **OpenAI** (GPT-4) - Balanced, excellent creativity
3. **Gemini** (Google) - Fast and efficient
4. **Groq** - Ultra-fast inference
5. **Nvidia** - Specialized models
6. **Mock** - Always available fallback

### Add an API Key

1. Get your API key from the provider
2. Edit `.env` file:

   ```text
   ANTHROPIC_API_KEY=sk-ant-xxxxx
   ```

3. Restart the system
4. Run `python main.py status` to verify

### Graceful Degradation

If an API key is invalid or the service fails:

```text
⚠ Anthropic error: Invalid API key. Falling back to mock.
Mock system activated. Continue working without interruption.
```

## Project Structure

```text
team-agent-system/
├── main.py                 # CLI entry point
├── llm_provider.py         # LLM routing & API calls
├── departments.py          # Department management
├── task_executor.py        # Task execution logic
├── config.json             # Department configuration (auto-created)
├── .env                    # API keys (create from .env.example)
├── .env.example            # API key template
├── data/
│   ├── tasks.json         # Task queue
│   ├── departments.json   # Department state (future)
│   └── results/           # Task results
└── README.md
```

## Configuration

Edit `config.json` to customize departments:

```json
{
  "departments": {
    "engineering": {
      "keywords": ["code", "bug", "feature", "deploy"],
      "staff": 5
    },
    "custom_dept": {
      "keywords": ["custom", "keywords"],
      "staff": 7
    }
  },
  "default_department": "engineering",
  "llm_provider": "auto"
}
```

## Commands

### Submit Task

```bash
python main.py submit "Task description" [--dept DEPARTMENT]
```

Submit a new task. Department auto-detected or specify with `--dept`.

### Process Tasks

```bash
python main.py process
```

Process all queued tasks using department heads & staff.

### List Tasks

```bash
python main.py list [--status STATUS]
```

List all tasks or filter by status (queued, completed).

### Show Task

```bash
python main.py show <task_id>
```

Display task details including results.

### System Status

```bash
python main.py status
```

Show system stats and current LLM provider.

## How It Works

### Task Flow

```text
1. User submits task
   ↓
2. Router detects department (based on keywords)
   ↓
3. Task queued
   ↓
4. Process command runs:
   - Department Head analyzes task
   - LLM generates strategy (or mock response)
   - 5 Staff members simulate work
   - Results compiled
   ↓
5. Task marked complete with results
```

### Without API Keys (Mock Mode)

```text
Task: "Fix login bug"
   ↓
Router: "engineering" (detected)
   ↓
Head Agent (Mock): "I'll break this into subtasks..."
   ↓
Staff Simulation:
   - Lead Dev: Architecture review
   - Backend Dev: API fix
   - Frontend Dev: UI check
   - QA Engineer: Testing
   - DevOps: Deployment
   ↓
Result: "Bug analyzed, fixed, tested, ready for production"
```

### With API Keys (Real LLM)

```text
Task: "Fix login bug"
   ↓
Router: "engineering" (detected)
   ↓
Head Agent (Claude/GPT): Real analysis via API
   ↓
Staff Simulation: (same as above)
   ↓
Result: Real LLM output integrated with staff sim
```

## Performance Notes

- **Mock Mode**: Instant responses, no network calls
- **API Mode**: Depends on provider (usually 1-5 seconds)
- **Task Processing**: Linear (one task at a time)

## Extending the System

### Add a New Department

```python
# In config.json, add:
"support_international": {
  "keywords": ["international", "language", "region"],
  "staff": 5
}
```

### Add Custom Staff Roles

Edit `task_executor.py` `staff_roles` dictionary:

```python
staff_roles = {
    "your_dept": ["Role1", "Role2", "Role3", "Role4", "Role5"]
}
```

### Custom Task Processing

Edit `_create_head_prompt()` in `task_executor.py` to change how heads approach tasks.

## Troubleshooting

### Tasks not processing?

```bash
python main.py list --status queued
# Check if tasks are queued
```

### API key not working?

```bash
python main.py status
# Should show fallback to mock if key is invalid
```

### Mock responses feel generic?

Add more specific mock templates in `llm_provider.py` `_mock_response()` method.

## Future Enhancements

- [ ] Parallel task processing
- [ ] Web UI dashboard
- [ ] Real staff member simulation (spawn subagents)
- [ ] Task dependencies & workflows
- [ ] Cost tracking per provider
- [ ] Advanced routing (skills, load balancing)
- [ ] Persistent result storage with search

## License

MIT
