"""
Agent Loop - the multi-turn cycle that turns a completion into an agent.

A single completion answers from whatever it already believes. A loop lets
the model say "I need the engineering budget", get the real number back,
and continue with it. That difference is the whole point: until now every
agent in this system produced prose about work rather than doing any, and
was scored an invariant 4.0 for it either way.

WHAT THIS DELIBERATELY DOES NOT DO: claim success when it ran out of road.
If the iteration cap is reached while the model still wants tools, the
result carries stopped_reason="max_iterations" and completed() is False.
This codebase has repeatedly shipped the opposite bug - escalated tasks
recorded as "completed", workflow steps marked done when execution
escalated - so a loop that quietly returned its last half-formed sentence
as a finished answer would be the same defect wearing a new hat.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools import ToolRegistry

# How much of a tool's output to keep in the per-call record. The full text
# always goes back to the model; this is only for humans reading a summary,
# where a 20KB JSON blob per call is noise.
RESULT_PREVIEW_CHARS = 200


@dataclass
class ToolCallRecord:
    """One tool invocation as it actually happened - including failures.

    Failed calls are recorded, not dropped: "the model asked for a tool
    that doesn't exist three times" is exactly the signal that tells you
    a tool's description is wrong, and it's invisible if only successes
    are kept.
    """
    name: str
    arguments: Dict[str, Any]
    ok: bool
    result_preview: str
    error: Optional[str] = None


@dataclass
class AgentRunResult:
    """Everything one agent run produced, including how it ended."""
    final_content: str
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    iterations: int = 0
    tokens_estimate: int = 0
    provider: str = "unknown"
    model: str = "unknown"
    stopped_reason: str = "completed"  # "completed" | "max_iterations"
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def completed(self) -> bool:
        """True only if the model stopped because it was done.

        Callers deciding whether a task succeeded must consult this rather
        than merely checking that final_content is non-empty.
        """
        return self.stopped_reason == "completed"

    def successful_tool_calls(self) -> int:
        return sum(1 for call in self.tool_calls if call.ok)


def run_agent_loop(prompt: str, provider, registry: ToolRegistry,
                   max_iterations: int = 5, task_type: str = "general") -> AgentRunResult:
    """Run one agent turn-cycle to completion or to the iteration cap.

    `provider` is anything exposing generate_with_tools(messages,
    tool_schemas, task_type) - LLMProvider does, and so does any test
    double, which is how this is tested without an API key.

    max_iterations bounds cost, not just runtime: every iteration is a
    full round trip billed against the department's budget, so an
    unbounded loop is a way to spend real money on a model that has
    decided to call list_departments forever.
    """
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be at least 1, got {max_iterations}")

    schemas = registry.schemas()
    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    tool_calls: List[ToolCallRecord] = []
    tokens = 0
    provider_used = "unknown"
    model_used = "unknown"
    last_text = ""

    for iteration in range(1, max_iterations + 1):
        response = provider.generate_with_tools(messages, schemas, task_type)

        tokens += response.get("tokens_estimate", 0) or 0
        provider_used = response.get("provider", provider_used)
        model_used = response.get("model", model_used)
        text = response.get("content", "") or ""
        requested = response.get("tool_calls") or []

        if text:
            last_text = text

        if not requested:
            return AgentRunResult(
                final_content=text,
                tool_calls=tool_calls,
                iterations=iteration,
                tokens_estimate=tokens,
                provider=provider_used,
                model=model_used,
                stopped_reason="completed",
                messages=messages + [{"role": "assistant", "content": text}],
            )

        messages.append({"role": "assistant", "content": text, "tool_calls": requested})

        for call in requested:
            arguments = call.get("arguments") or {}
            result = registry.execute(call["name"], arguments)
            tool_calls.append(ToolCallRecord(
                name=call["name"],
                arguments=arguments,
                ok=result.ok,
                result_preview=result.content[:RESULT_PREVIEW_CHARS],
                error=result.error,
            ))
            # A failed tool result is fed back verbatim rather than raising:
            # the model can read "no such tool: x" and correct itself, which
            # is the entire reason ToolRegistry.execute never throws.
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "name": call["name"],
                "content": result.content,
            })

    return AgentRunResult(
        final_content=last_text,
        tool_calls=tool_calls,
        iterations=max_iterations,
        tokens_estimate=tokens,
        provider=provider_used,
        model=model_used,
        stopped_reason="max_iterations",
        messages=messages,
    )
