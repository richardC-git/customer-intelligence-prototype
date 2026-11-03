"""Provider-agnostic language model boundary.

The service is chosen by ``LLM_PROVIDER`` and authenticated with ``LLM_API_KEY``.
Features use ``GuardedLLM`` (see ``guard.py``), never an adapter directly.

``LLMRequest`` has no field for tools, functions, or web access, so no adapter
can give a model the ability to act: it can only return text.
"""

import os
from dataclasses import dataclass
from typing import Any, Protocol

PROVIDER_ENV_VAR = "LLM_PROVIDER"
MODEL_ENV_VAR = "LLM_MODEL"
API_KEY_ENV_VAR = "LLM_API_KEY"
BASE_URL_ENV_VAR = "LLM_BASE_URL"

# The project's Claude credit is limited, so only the cheapest model may be used.
ALLOWED_MODELS = {"anthropic": ("claude-haiku-5-5",)}


class LLMError(RuntimeError):
    """Raised when a model cannot be called or its response is unusable."""


class LLMConfigError(LLMError):
    """Raised when LLM settings are missing or invalid."""


class LLMRefusedError(LLMError):
    """Raised when the model or its provider's safety system declined the request."""


class LLMTruncatedError(LLMError):
    """Raised when the model hit its output limit, so its JSON is incomplete."""


class LLMPolicyError(LLMError):
    """Raised when the safety policy blocked a request or a model's output."""


@dataclass(frozen=True)
class LLMRequest:
    """One self-contained model call; conversations are never carried over."""

    system: str
    instructions: str
    context: str
    schema: dict[str, Any]
    max_output_tokens: int = 8192

    @property
    def user_message(self) -> str:
        return f"{self.instructions}\n\n{self.context}"


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    """Model adapters implement this contract and return the model's raw text."""

    name: str
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        ...


def provider_from_env() -> LLMProvider:
    """Build the unguarded adapter named by LLM_PROVIDER. Features use ``llm_from_env``."""
    from sfs_intelligence.llm.anthropic import AnthropicClient
    from sfs_intelligence.llm.copilot import GitHubCopilotClient
    from sfs_intelligence.llm.gemini import GeminiClient
    from sfs_intelligence.llm.m365 import M365CopilotClient
    from sfs_intelligence.llm.openai import AzureOpenAIClient, OpenAIClient

    providers = {
        "anthropic": AnthropicClient,
        "azure": AzureOpenAIClient,
        "copilot": GitHubCopilotClient,
        "gemini": GeminiClient,
        "m365-copilot": M365CopilotClient,
        "openai": OpenAIClient,
    }

    name = os.environ.get(PROVIDER_ENV_VAR, "").strip().lower()
    if not name:
        raise LLMConfigError(f"{PROVIDER_ENV_VAR} is not set. Add it to your .env file.")
    if name not in providers:
        supported = ", ".join(sorted(providers))
        raise LLMConfigError(f"Unknown {PROVIDER_ENV_VAR} {name!r}. Supported: {supported}.")

    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise LLMConfigError(f"{API_KEY_ENV_VAR} is not set. Add it to your .env file.")

    # Microsoft 365 Copilot chooses its own model, so LLM_MODEL does not apply.
    model = os.environ.get(MODEL_ENV_VAR, "").strip()
    if not model and name != "m365-copilot":
        raise LLMConfigError(f"{MODEL_ENV_VAR} is not set. Add it to your .env file.")

    allowed = ALLOWED_MODELS.get(name)
    if allowed and model not in allowed:
        raise LLMConfigError(
            f"{MODEL_ENV_VAR} {model!r} is not allowed for {name}. Allowed: {', '.join(allowed)}."
        )

    options = {"api_key": api_key, "model": model}
    base_url = os.environ.get(BASE_URL_ENV_VAR, "").strip()
    if base_url:
        if name == "copilot":
            raise LLMConfigError(f"{BASE_URL_ENV_VAR} does not apply to the copilot provider.")
        options["base_url"] = base_url
    return providers[name](**options)
