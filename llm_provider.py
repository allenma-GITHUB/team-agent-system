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
