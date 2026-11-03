"""GitHub Copilot adapter using the official Copilot SDK (optional dependency).

The SDK drives a coding agent that can normally run shell commands and edit
files, which must never be reachable from text found on a web page. The session
is therefore locked down: empty mode, no tools, every permission request
rejected, and an empty temporary directory. If a tool starts anyway the
response is discarded.

``LLM_API_KEY`` is a GitHub token for an account with Copilot access.
"""

import asyncio
import json
import logging
import tempfile
from typing import Any

from sfs_intelligence.llm.base import (
    LLMConfigError,
    LLMError,
    LLMPolicyError,
    LLMRequest,
    LLMResponse,
)

# The SDK logs failures with tracebacks; they are reported as LLMError instead.
logging.getLogger("copilot").addHandler(logging.NullHandler())

INSTALL_HINT = 'The GitHub Copilot SDK is not installed. Run: pip install -e ".[copilot]"'


class GitHubCopilotClient:
    name = "copilot"

    def __init__(self, api_key: str, model: str, timeout: float = 120.0) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            import copilot
            from copilot.generated.rpc import PermissionDecisionReject
        except ImportError:
            raise LLMConfigError(INSTALL_HINT) from None

        try:
            return asyncio.run(self._complete(copilot, PermissionDecisionReject, request))
        except LLMError:
            raise
        except Exception as error:
            reason = str(error).replace(self._api_key, "[REDACTED]")
            raise LLMError(f"GitHub Copilot call failed: {reason}") from None

    async def _complete(self, sdk: Any, reject: Any, request: LLMRequest) -> LLMResponse:
        tool_events: list[str] = []

        def record_tools(event: Any) -> None:
            if str(getattr(event.type, "value", event.type)).startswith("tool."):
                tool_events.append(str(event.type))

        with tempfile.TemporaryDirectory(prefix="sfs-copilot-") as workspace:
            client = sdk.CopilotClient(
                mode="empty",
                github_token=self._api_key,
                base_directory=workspace,
                use_logged_in_user=False,
            )
            async with client:
                session = await client.create_session(
                    model=self.model,
                    github_token=self._api_key,
                    available_tools=[],
                    on_permission_request=lambda *_: reject(feedback="Tools are disabled."),
                    system_message={"mode": "replace", "content": request.system},
                    working_directory=workspace,
                    enable_skills=False,
                    enable_config_discovery=False,
                    enable_file_hooks=False,
                    on_event=record_tools,
                )
                async with session:
                    reply = await session.send_and_wait(
                        request.user_message,
                        response_schema=request.schema,
                        timeout=self._timeout,
                    )

        if tool_events:
            raise LLMPolicyError("GitHub Copilot attempted to use a tool; response discarded.")
        text = getattr(getattr(reply, "data", None), "content", None)
        if not text:
            raise LLMError("GitHub Copilot returned no text.")
        if not isinstance(text, str):
            text = json.dumps(text)
        return LLMResponse(text=text, model=self.model)
