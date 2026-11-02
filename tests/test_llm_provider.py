import pytest

from sfs_intelligence.llm.anthropic import AnthropicClient
from sfs_intelligence.llm.base import LLMConfigError, LLMPolicyError, provider_from_env
from sfs_intelligence.llm.copilot import GitHubCopilotClient
from sfs_intelligence.llm.gemini import GeminiClient
from sfs_intelligence.llm.guard import GuardedLLM, UntrustedDocument, llm_from_env
from sfs_intelligence.llm.m365 import M365CopilotClient
from sfs_intelligence.llm.openai import AzureOpenAIClient, OpenAIClient
from sfs_intelligence.models.prospect import ProspectAnalysis


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_ALLOW_INTERNAL_DATA",
    ):
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch, **settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("openai", OpenAIClient),
        ("gemini", GeminiClient),
        ("copilot", GitHubCopilotClient),
        ("  OpenAI ", OpenAIClient),
    ],
)
def test_builds_the_named_provider(monkeypatch, name, expected):
    configure(monkeypatch, LLM_PROVIDER=name, LLM_MODEL="m", LLM_API_KEY="k")

    provider = provider_from_env()

    assert type(provider) is expected
    assert provider.model == "m"


def test_builds_claude_with_the_cheap_model(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="anthropic", LLM_MODEL="claude-haiku-5-5", LLM_API_KEY="k")

    assert type(provider_from_env()) is AnthropicClient


@pytest.mark.parametrize(
    "model", ["claude-opus-5-5", "claude-sonnet-4-6", "claude-fable-5-1", "claude-haiku-5-5-x"]
)
def test_expensive_claude_models_are_refused(monkeypatch, model):
    configure(monkeypatch, LLM_PROVIDER="anthropic", LLM_MODEL=model, LLM_API_KEY="k")

    with pytest.raises(LLMConfigError, match="not allowed"):
        provider_from_env()


def test_builds_azure_with_base_url(monkeypatch):
    configure(
        monkeypatch,
        LLM_PROVIDER="azure",
        LLM_MODEL="deployment",
        LLM_API_KEY="k",
        LLM_BASE_URL="https://example.openai.azure.com/openai/v1",
    )

    assert type(provider_from_env()) is AzureOpenAIClient


def test_azure_without_base_url_explains_itself(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="azure", LLM_MODEL="d", LLM_API_KEY="k")

    with pytest.raises(LLMConfigError, match="LLM_BASE_URL"):
        provider_from_env()


def test_m365_does_not_need_a_model(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="m365-copilot", LLM_API_KEY="token")

    provider = provider_from_env()

    assert type(provider) is M365CopilotClient
    assert provider.model == "microsoft-365-copilot"


def test_openai_compatible_services_use_base_url(monkeypatch):
    configure(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="m",
        LLM_API_KEY="k",
        LLM_BASE_URL="http://localhost:11434/v1",
    )

    assert type(provider_from_env()) is OpenAIClient


def test_copilot_rejects_base_url(monkeypatch):
    configure(
        monkeypatch,
        LLM_PROVIDER="copilot",
        LLM_MODEL="m",
        LLM_API_KEY="k",
        LLM_BASE_URL="https://example.com",
    )

    with pytest.raises(LLMConfigError, match="does not apply"):
        provider_from_env()


def test_requires_provider():
    with pytest.raises(LLMConfigError, match="LLM_PROVIDER is not set"):
        provider_from_env()


def test_rejects_unknown_provider(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="skynet", LLM_MODEL="m", LLM_API_KEY="k")

    with pytest.raises(LLMConfigError, match="Supported: anthropic, azure, copilot, gemini"):
        provider_from_env()


def test_requires_key(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="openai", LLM_MODEL="m", LLM_API_KEY="  ")

    with pytest.raises(LLMConfigError, match="LLM_API_KEY is not set"):
        provider_from_env()


def test_requires_model(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="openai", LLM_API_KEY="k")

    with pytest.raises(LLMConfigError, match="LLM_MODEL is not set"):
        provider_from_env()


def test_rejects_insecure_base_url(monkeypatch):
    configure(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="m",
        LLM_API_KEY="k",
        LLM_BASE_URL="http://attacker.example/v1",
    )

    with pytest.raises(LLMConfigError, match="https"):
        provider_from_env()


def test_llm_from_env_always_applies_the_guard(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="openai", LLM_MODEL="m", LLM_API_KEY="k")

    guarded = llm_from_env()

    assert isinstance(guarded, GuardedLLM)
    assert guarded.label == "openai, m"


@pytest.mark.parametrize("value", ["", "false", "no", "1", "yes"])
def test_internal_data_stays_blocked_unless_exactly_true(monkeypatch, value):
    configure(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="m",
        LLM_API_KEY="k",
        LLM_ALLOW_INTERNAL_DATA=value,
    )
    document = UntrustedDocument("I1", "CRM record", "Turnover data", internal=True)

    with pytest.raises(LLMPolicyError, match="Internal data"):
        llm_from_env().analyze("prospect_profile", "Acme", [document], ProspectAnalysis)
