"""GitHub Copilot adapter tests against a stand-in for the optional SDK.

The stand-in records every option the adapter passes, because this adapter's
safety depends on those options switching the SDK's agent powers off.
"""

import inspect
import sys
import types
from dataclasses import dataclass

import pytest

from sfs_intelligence.llm.base import LLMConfigError, LLMError, LLMPolicyError, LLMRequest
from sfs_intelligence.llm.copilot import GitHubCopilotClient

REQUEST = LLMRequest("SYSTEM POLICY", "TASK", "CONTEXT", {"type": "object"})


@dataclass
class Event:
    type: str
    data: object = None


class FakeSession:
    def __init__(self, sdk, options):
        self.sdk = sdk
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.sdk.session_closed = True

    async def send_and_wait(self, prompt, **options):
        self.sdk.prompt = prompt
        self.sdk.send_options = options
        if self.sdk.error:
            raise self.sdk.error
        for event in self.sdk.events:
            self.options["on_event"](event)
        return self.sdk.reply


class FakeClient:
    def __init__(self, sdk, **options):
        self.sdk = sdk
        sdk.client_options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.sdk.client_stopped = True

    async def create_session(self, **options):
        self.sdk.session_options = options
        return FakeSession(self.sdk, options)


class Rejection:
    def __init__(self, feedback=None):
        self.feedback = feedback


@pytest.fixture
def sdk(monkeypatch):
    package = types.ModuleType("copilot")
    package.reply = types.SimpleNamespace(data=types.SimpleNamespace(content='{"answer": 1}'))
    package.events = []
    package.error = None
    package.CopilotClient = lambda **options: FakeClient(package, **options)
    generated = types.ModuleType("copilot.generated")
    rpc = types.ModuleType("copilot.generated.rpc")
    rpc.PermissionDecisionReject = Rejection
    monkeypatch.setitem(sys.modules, "copilot", package)
    monkeypatch.setitem(sys.modules, "copilot.generated", generated)
    monkeypatch.setitem(sys.modules, "copilot.generated.rpc", rpc)
    return package


def client():
    return GitHubCopilotClient("ghp_token", "gpt-test")


def test_returns_the_model_reply(sdk):
    response = client().complete(REQUEST)

    assert response.text == '{"answer": 1}'
    assert response.model == "gpt-test"
    assert sdk.prompt == "TASK\n\nCONTEXT"
    assert sdk.send_options["response_schema"] == {"type": "object"}


def test_client_is_isolated_from_the_host(sdk):
    client().complete(REQUEST)

    options = sdk.client_options
    assert options["mode"] == "empty"
    assert options["github_token"] == "ghp_token"
    assert options["use_logged_in_user"] is False
    assert "sfs-copilot-" in options["base_directory"]


def test_session_has_no_tools_and_replaces_the_agent_system_prompt(sdk):
    client().complete(REQUEST)

    options = sdk.session_options
    assert options["available_tools"] == []
    assert options["github_token"] == "ghp_token"
    assert options["system_message"] == {"mode": "replace", "content": "SYSTEM POLICY"}
    assert options["enable_skills"] is False
    assert options["enable_config_discovery"] is False
    assert options["enable_file_hooks"] is False


def test_every_permission_request_is_rejected(sdk):
    client().complete(REQUEST)

    decision = sdk.session_options["on_permission_request"]({"kind": "shell"}, {})

    assert isinstance(decision, Rejection)


def test_response_is_discarded_if_a_tool_starts_anyway(sdk):
    sdk.events = [Event(types.SimpleNamespace(value="tool.execution_start"))]

    with pytest.raises(LLMPolicyError, match="attempted to use a tool"):
        client().complete(REQUEST)


def test_other_events_are_ignored(sdk):
    sdk.events = [Event(types.SimpleNamespace(value="assistant.message"))]

    assert client().complete(REQUEST).text


def test_sessions_are_closed_and_workspace_removed(sdk, tmp_path):
    from pathlib import Path

    client().complete(REQUEST)

    assert sdk.session_closed and sdk.client_stopped
    assert not Path(sdk.client_options["base_directory"]).exists()


def test_empty_reply_is_an_error(sdk):
    sdk.reply = None

    with pytest.raises(LLMError, match="no text"):
        client().complete(REQUEST)


def test_sdk_errors_are_reported_without_the_token(sdk):
    sdk.error = RuntimeError("auth failed for ghp_token")

    with pytest.raises(LLMError) as caught:
        client().complete(REQUEST)

    assert "ghp_token" not in str(caught.value)


def test_adapter_matches_the_real_sdk():
    """Only the real SDK can prove the stand-in above reflects it; skipped if not installed."""
    sdk = pytest.importorskip("copilot")
    rpc = pytest.importorskip("copilot.generated.rpc")

    client_options = inspect.signature(sdk.CopilotClient.__init__).parameters
    session_options = inspect.signature(sdk.CopilotClient.create_session).parameters
    send_options = inspect.signature(sdk.CopilotSession.send_and_wait).parameters

    assert {"mode", "github_token", "base_directory", "use_logged_in_user"} <= set(client_options)
    assert {
        "github_token",
        "available_tools",
        "on_permission_request",
        "system_message",
        "working_directory",
        "enable_skills",
        "enable_config_discovery",
        "enable_file_hooks",
        "on_event",
    } <= set(session_options)
    assert {"response_schema", "timeout"} <= set(send_options)
    assert rpc.PermissionDecisionReject(feedback="no").kind == "reject"


def test_missing_sdk_explains_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "copilot", None)

    with pytest.raises(LLMConfigError, match=r"\.\[copilot\]"):
        client().complete(REQUEST)
