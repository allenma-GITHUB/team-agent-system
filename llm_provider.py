"""
LLM Provider - Routes to available LLM APIs or mocks if none available.
Gracefully degrades: if API keys missing, uses mock responses.
"""
import os
import json
from typing import Optional, Dict, Any
from pathlib import Path

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="google")
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class LLMProvider:
    """Routes tasks to available LLM providers."""

    def __init__(self):
        self.load_env()
        self.detect_providers()
        self.primary_provider = self.select_provider()

    def load_env(self):
        """Load API keys from .env file."""
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

    def detect_providers(self):
        """Detect which LLM providers are available."""
        self.available = {}

        if os.getenv("ANTHROPIC_API_KEY") and HAS_ANTHROPIC:
            self.available["anthropic"] = True

        if os.getenv("OPENAI_API_KEY") and HAS_OPENAI:
            self.available["openai"] = True

        if os.getenv("GEMINI_API_KEY") and HAS_GEMINI:
            self.available["gemini"] = True

        if os.getenv("GROQ_API_KEY") and HAS_REQUESTS:
            self.available["groq"] = True

        if os.getenv("NVIDIA_API_KEY") and HAS_REQUESTS:
            self.available["nvidia"] = True

        # Always have mock as fallback
        self.available["mock"] = True

    def select_provider(self, task_type: str = "general") -> str:
        """Select best provider for task type.

        For coding tasks: Prioritize Claude Sonnet (token-efficient, great code)
        For general: Balance cost and capability
        """
        if task_type in ["engineering", "coding", "development"]:
            # Coding tasks: prefer Sonnet (token-efficient, excellent code quality)
            preference = ["anthropic", "groq", "openai", "gemini", "nvidia"]
        else:
            # General: prioritize capability
            preference = ["anthropic", "openai", "gemini", "groq", "nvidia"]

        for provider in preference:
            if self.available.get(provider):
                return provider

        return "mock"

    def get_status(self) -> str:
        """Get current provider status."""
        available = [p for p, v in self.available.items() if v]
        if self.primary_provider == "mock":
            return f"MOCK (available: {', '.join(available)})"
        return f"{self.primary_provider.upper()} (alternatives: {', '.join([p for p in available if p != self.primary_provider])})"

    def generate(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Generate response from best provider for task type.

        Returns: {
            "content": "response text",
            "provider": "provider used",
            "tokens_estimated": estimated tokens used,
            "model": "model used"
        }
        """
        # Update provider selection based on task type
        self.primary_provider = self.select_provider(task_type)

        if self.primary_provider == "anthropic":
            return self._call_anthropic(prompt, task_type)
        elif self.primary_provider == "openai":
            return self._call_openai(prompt, task_type)
        elif self.primary_provider == "gemini":
            return self._call_gemini(prompt, task_type)
        elif self.primary_provider == "groq":
            return self._call_groq(prompt, task_type)
        elif self.primary_provider == "nvidia":
            return self._call_nvidia(prompt, task_type)
        else:
            return self._mock_response(prompt, task_type)

    def _call_anthropic(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Call Anthropic Claude API with model fallback strategy.

        Priority: Sonnet 5 (best for coding, token-efficient)
        Fallback: Opus 5 (more capable if needed)
        """
        models_to_try = [
            ("claude-sonnet-5", "Claude Sonnet 5"),
            ("claude-opus-5", "Claude Opus 5"),
        ]

        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
        )

        for model, model_name in models_to_try:
            try:
                message = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}]
                )
                tokens_estimate = message.usage.input_tokens + message.usage.output_tokens
                print(f"✓ Using {model_name}")
                text = "".join(b.text for b in message.content if b.type == "text")
                return {
                    "content": text,
                    "provider": "anthropic",
                    "model": model_name,
                    "tokens_estimate": tokens_estimate
                }
            except anthropic.NotFoundError:
                continue  # Try next model
            except anthropic.APIError as e:
                if "overloaded" in str(e).lower():
                    continue
                print(f"⚠ Anthropic API error on {model_name}: {str(e)[:80]}")
                continue
            except Exception as e:
                print(f"⚠ Unexpected error: {str(e)[:80]}")
                break

        # All models failed
        print(f"⚠ All Anthropic models unavailable. Falling back to mock.")
        self.primary_provider = "mock"
        return self._mock_response(prompt, task_type)

    def _call_openai(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Call OpenAI API with model fallback."""
        models_to_try = [
            ("gpt-4o", "GPT-4o (Recommended)"),
            ("gpt-4-turbo", "GPT-4 Turbo"),
            ("gpt-3.5-turbo", "GPT-3.5 Turbo (Fast, cheap)"),
        ]

        client = openai.OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        )

        for model, model_name in models_to_try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=2048
                )
                tokens_estimate = response.usage.prompt_tokens + response.usage.completion_tokens
                print(f"✓ Using {model_name}")
                return {
                    "content": response.choices[0].message.content,
                    "provider": "openai",
                    "model": model_name,
                    "tokens_estimate": tokens_estimate
                }
            except Exception as e:
                if "not_found" in str(e).lower() or "does not exist" in str(e).lower():
                    continue
                print(f"⚠ OpenAI error on {model}: {str(e)[:80]}")
                if "insufficient_quota" in str(e).lower():
                    break
                continue

        print(f"⚠ All OpenAI models failed. Falling back to mock.")
        self.primary_provider = "mock"
        return self._mock_response(prompt, task_type)

    def _call_gemini(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Call Google Gemini API."""
        try:
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            # Token estimate (rough)
            tokens_estimate = len(prompt.split()) + len(response.text.split())
            print(f"✓ Using Gemini 2.0 Flash")
            return {
                "content": response.text,
                "provider": "gemini",
                "model": "Gemini 2.0 Flash",
                "tokens_estimate": tokens_estimate
            }
        except Exception as e:
            print(f"⚠ Gemini error: {str(e)[:80]}. Falling back to mock.")
            self.primary_provider = "mock"
            return self._mock_response(prompt, task_type)

    def _call_groq(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Call Groq API (ultra-fast inference)."""
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
                "temperature": 0.7
            }
            response = requests.post(url, json=data, headers=headers, timeout=30)
            result = response.json()
            tokens_estimate = result.get("usage", {}).get("total_tokens", len(prompt.split()))
            print(f"✓ Using Groq Mixtral 8x7B (ultra-fast)")
            return {
                "content": result["choices"][0]["message"]["content"],
                "provider": "groq",
                "model": "Mixtral 8x7B",
                "tokens_estimate": tokens_estimate
            }
        except Exception as e:
            print(f"⚠ Groq error: {str(e)[:80]}. Falling back to mock.")
            self.primary_provider = "mock"
            return self._mock_response(prompt, task_type)

    def _call_nvidia(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Call Nvidia API with streaming support."""
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=os.getenv("NVIDIA_API_KEY")
            )

            response = client.chat.completions.create(
                model="nvidia/nemotron-3-ultra-550b-a55b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                top_p=0.95,
                max_tokens=2048,
                stream=False
            )
            tokens_estimate = response.usage.prompt_tokens + response.usage.completion_tokens
            print(f"✓ Using Nvidia Nemotron 550B")
            return {
                "content": response.choices[0].message.content,
                "provider": "nvidia",
                "model": "Nemotron 3 Ultra 550B",
                "tokens_estimate": tokens_estimate
            }
        except Exception as e:
            print(f"⚠ Nvidia error: {str(e)[:80]}. Falling back to mock.")
            self.primary_provider = "mock"
            return self._mock_response(prompt, task_type)

    def _mock_response(self, prompt: str, task_type: str = "general") -> Dict[str, Any]:
        """Generate mock response (no API required)."""
        prompts_lower = prompt.lower()

        if any(word in prompts_lower for word in ["fix", "bug", "error", "deploy", "code"]):
            content = "MOCK: Analyzed the bug - found root cause in database query. Fixed by adding proper indexing. Tested locally and in staging. Ready for production."

        elif any(word in prompts_lower for word in ["design", "ui", "mockup", "ux"]):
            content = "MOCK: Created design mockups with wireframes, color palette, and component library. Follows accessibility standards (WCAG 2.1). Ready for development handoff."

        elif any(word in prompts_lower for word in ["research", "analyze", "data", "trend"]):
            content = "MOCK: Completed analysis of available data. Key findings: 85% positive sentiment, 40% improvement potential. Highest impact: customer onboarding. Detailed report generated."

        elif any(word in prompts_lower for word in ["sales", "customer", "proposal", "pitch"]):
            content = "MOCK: Prepared customer proposal with ROI analysis and implementation timeline. Total cost: $25K, 3-month deployment. Includes post-sale support."

        else:
            content = "MOCK: Task completed successfully. Generated comprehensive analysis with actionable recommendations."

        return {
            "content": content,
            "provider": "mock",
            "model": "MockLLM",
            "tokens_estimate": len(prompt.split()) + len(content.split())
        }

    # ------------------------------------------------------------------
    # Tool calling
    #
    # Everything above is single-turn: one prompt in, one block of prose
    # out. Everything below lets a model ask for a tool, receive its real
    # result, and keep going - which is the difference between an agent
    # that describes looking up a budget and one that actually looks it up.
    #
    # The conversation is carried in a PROVIDER-NEUTRAL message format so
    # agent_loop.py never learns any vendor's wire shape:
    #   {"role": "user",      "content": str}
    #   {"role": "assistant", "content": str, "tool_calls": [
    #        {"id": str, "name": str, "arguments": dict}]}
    #   {"role": "tool",      "tool_call_id": str, "name": str, "content": str}
    # Translation into each vendor's shape lives in the pure module-level
    # functions at the bottom of this file, so they can be tested without
    # an API key - which matters here, because this container has none and
    # the mock path is the only one CI ever exercises.
    # ------------------------------------------------------------------

    TOOL_CAPABLE_PROVIDERS = ("anthropic", "openai", "mock")

    def supports_tools(self, task_type: str = "general") -> bool:
        """Whether the provider selected for this task type can call tools."""
        return self.select_provider(task_type) in self.TOOL_CAPABLE_PROVIDERS

    def generate_with_tools(self, messages: list, tool_schemas: list,
                            task_type: str = "general") -> Dict[str, Any]:
        """One turn of a tool-calling conversation.

        Returns a normalized dict:
            {"content": str,            # assistant prose, may be ""
             "tool_calls": [{"id", "name", "arguments"}],
             "provider", "model", "tokens_estimate"}

        Deliberately does NOT fall back to mock when a real provider
        fails, unlike generate(). A mock turn invents which tools to call;
        silently substituting that for a failed real call would report
        fabricated agent reasoning as if the model had chosen it - the
        same class of quiet dishonesty as marking an escalated task
        "completed". A caller that wants mock behavior can ask for it.
        """
        self.primary_provider = self.select_provider(task_type)

        if self.primary_provider == "anthropic":
            return self._call_anthropic_tools(messages, tool_schemas)
        if self.primary_provider == "openai":
            return self._call_openai_tools(messages, tool_schemas)
        if self.primary_provider == "mock":
            return self._mock_tool_response(messages, tool_schemas)

        raise ToolsUnsupportedError(
            f"Provider '{self.primary_provider}' has no tool-calling support in this system. "
            f"Tool-capable providers: {', '.join(self.TOOL_CAPABLE_PROVIDERS)}."
        )

    def _call_anthropic_tools(self, messages: list, tool_schemas: list) -> Dict[str, Any]:
        """One Anthropic turn with tools, same model-fallback order as _call_anthropic."""
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
        )

        last_error = None
        for model, model_name in (("claude-sonnet-5", "Claude Sonnet 5"),
                                   ("claude-opus-5", "Claude Opus 5")):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    messages=to_anthropic_messages(messages),
                    tools=to_anthropic_tools(tool_schemas),
                )
            except anthropic.NotFoundError as exc:
                last_error = exc
                continue
            except anthropic.APIError as exc:
                last_error = exc
                if "overloaded" in str(exc).lower():
                    continue
                raise ToolCallFailedError(f"Anthropic API error on {model_name}: {exc}") from exc

            text = "".join(b.text for b in response.content if b.type == "text")
            tool_calls = [
                {"id": b.id, "name": b.name, "arguments": b.input}
                for b in response.content if b.type == "tool_use"
            ]
            return {
                "content": text,
                "tool_calls": tool_calls,
                "provider": "anthropic",
                "model": model_name,
                "tokens_estimate": response.usage.input_tokens + response.usage.output_tokens,
            }

        raise ToolCallFailedError(f"No Anthropic model available for tool use: {last_error}")

    def _call_openai_tools(self, messages: list, tool_schemas: list) -> Dict[str, Any]:
        """One OpenAI turn with tools, same model-fallback order as _call_openai."""
        client = openai.OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        )

        last_error = None
        for model, model_name in (("gpt-4o", "GPT-4o"), ("gpt-4-turbo", "GPT-4 Turbo")):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=to_openai_messages(messages),
                    tools=to_openai_tools(tool_schemas),
                    max_tokens=2048,
                )
            except Exception as exc:  # noqa: BLE001 - try the next model, then report
                last_error = exc
                continue

            choice = response.choices[0].message
            tool_calls = [
                {
                    "id": call.id,
                    "name": call.function.name,
                    # OpenAI sends arguments as a JSON *string*; a model can emit
                    # malformed JSON, and that must reach the tool layer as an
                    # ordinary bad-arguments result rather than exploding here.
                    "arguments": _safe_json_object(call.function.arguments),
                }
                for call in (choice.tool_calls or [])
            ]
            usage = getattr(response, "usage", None)
            return {
                "content": choice.content or "",
                "tool_calls": tool_calls,
                "provider": "openai",
                "model": model_name,
                "tokens_estimate": (usage.prompt_tokens + usage.completion_tokens) if usage else 0,
            }

        raise ToolCallFailedError(f"No OpenAI model available for tool use: {last_error}")

    def _mock_tool_response(self, messages: list, tool_schemas: list) -> Dict[str, Any]:
        """Deterministic tool-calling mock, so the loop is exercisable and
        testable with no API key at all - the only path this container and
        the test suite can actually run.

        Behaviour: on the first turn it requests the tool whose subject the
        prompt mentions; once any tool result is present in the history it
        answers in prose and stops. That terminates in two turns, which is
        what makes a max-iterations test meaningful rather than accidental.
        """
        available = {schema["name"] for schema in tool_schemas}
        already_used_tools = any(m.get("role") == "tool" for m in messages)

        if already_used_tools or not available:
            observed = [m for m in messages if m.get("role") == "tool"]
            return {
                "content": ("MOCK: answered using " + ", ".join(m.get("name", "?") for m in observed)
                            if observed else "MOCK: answered without tools."),
                "tool_calls": [],
                "provider": "mock",
                "model": "MockLLM",
                "tokens_estimate": _mock_token_estimate(messages),
            }

        text = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user").lower()

        if "budget" in text or "afford" in text or "cost" in text:
            wanted, args = "get_department_budget", {"department": _mock_pick_department(text)}
        elif any(word in text for word in ("capacity", "workload", "staff", "busy")):
            wanted, args = "get_department_capacity", {"department": _mock_pick_department(text)}
        elif "task" in text or "queue" in text:
            wanted, args = "list_tasks", {}
        else:
            wanted, args = "list_departments", {}

        # Fall back to whatever the registry does offer, so a caller with a
        # custom toolset still gets a tool call rather than silence.
        if wanted not in available:
            wanted, args = sorted(available)[0], {}

        return {
            "content": "",
            "tool_calls": [{"id": f"mock_call_{len(messages)}", "name": wanted, "arguments": args}],
            "provider": "mock",
            "model": "MockLLM",
            "tokens_estimate": _mock_token_estimate(messages),
        }


class ToolsUnsupportedError(RuntimeError):
    """Raised when the selected provider cannot call tools in this system."""


class ToolCallFailedError(RuntimeError):
    """Raised when a tool-capable provider was reachable but the call failed."""


def _safe_json_object(raw: str) -> Dict[str, Any]:
    """Parse a model-supplied JSON argument string, tolerating garbage.

    Returns {} for anything that isn't a JSON object; the tool layer then
    reports it as missing/unexpected arguments, which the model can read
    and correct on the next turn.
    """
    try:
        parsed = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mock_token_estimate(messages: list) -> int:
    return sum(len(str(m.get("content", "")).split()) for m in messages)


def _mock_pick_department(text: str) -> str:
    # Local import: keeps this module's own import surface stdlib-only,
    # which is the property that lets it be imported anywhere without
    # dragging the domain layer along.
    from departments import DepartmentManager
    for dept in DepartmentManager.get_departments():
        if dept.lower() in text:
            return dept
    return "engineering"


def to_anthropic_tools(tool_schemas: list) -> list:
    """Anthropic already uses {name, description, input_schema} - the shape
    ToolRegistry.schemas() emits - so this is a defensive copy, not a rewrite."""
    return [
        {
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["input_schema"],
        }
        for schema in tool_schemas
    ]


def to_openai_tools(tool_schemas: list) -> list:
    """OpenAI wraps the same information in a 'function' envelope."""
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["input_schema"],
            },
        }
        for schema in tool_schemas
    ]


def to_anthropic_messages(messages: list) -> list:
    """Neutral messages -> Anthropic content blocks.

    Two rules this encodes, both of which the API enforces and neither of
    which is obvious:
      1. An assistant turn that called tools is a list of blocks (optional
         text, then one tool_use block per call) - and an empty text block
         is rejected outright, so it is omitted rather than sent blank.
      2. Every tool_result answering one assistant turn must arrive in a
         SINGLE user message. Consecutive tool messages are therefore
         merged, not emitted one per message - sending them separately is
         the most common way a hand-rolled loop breaks on the second
         parallel tool call.
    """
    out = []
    pending_results = []

    def flush_results():
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        role = message.get("role")

        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id"),
                "content": str(message.get("content", "")),
            })
            continue

        flush_results()

        if role == "assistant":
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                blocks.append({
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["name"],
                    "input": call.get("arguments", {}),
                })
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": "user", "content": str(message.get("content", ""))})

    flush_results()
    return out


def to_openai_messages(messages: list) -> list:
    """Neutral messages -> OpenAI chat format.

    Unlike Anthropic, tool results are their own role and are NOT merged;
    each carries the tool_call_id it answers.
    """
    out = []
    for message in messages:
        role = message.get("role")

        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": message.get("tool_call_id"),
                "content": str(message.get("content", "")),
            })
        elif role == "assistant":
            entry = {"role": "assistant", "content": message.get("content") or None}
            if message.get("tool_calls"):
                entry["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call.get("arguments", {})),
                        },
                    }
                    for call in message["tool_calls"]
                ]
            out.append(entry)
        else:
            out.append({"role": "user", "content": str(message.get("content", ""))})
    return out
