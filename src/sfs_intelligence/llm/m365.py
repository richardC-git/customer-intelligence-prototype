"""Microsoft 365 Copilot adapter for the Chat API (Microsoft Graph beta, preview).

Differences from the other adapters, all forced by this API:

- ``LLM_API_KEY`` is a delegated Graph access token for a licensed user. There
  is no app-only access and tokens expire, typically within about an hour.
- It has no system prompt or schema enforcement, so both go in the message.
  Documents go in ``additionalContext``, which is meant for grounding data.
- Web grounding can be turned off per message, but searching the user's company
  data cannot. Text on a web page could steer that search, so a reply built on
  company documents or carrying a sensitivity label is rejected, not shown.

Reply parsing follows Microsoft's documented examples; it has not been run
against a live tenant.
"""

import json
import re
from typing import Any
from urllib.parse import quote

from sfs_intelligence.llm.base import LLMError, LLMPolicyError, LLMRequest, LLMResponse
from sfs_intelligence.llm.http import JsonClient, clean_base_url

DEFAULT_BASE_URL = "https://graph.microsoft.com/beta"
MODEL_NAME = "microsoft-365-copilot"

_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_CITATION_MARKER = re.compile(r"\[\^\d+\^\]")
_ENTITY_TAG = re.compile(r"</?(?:Person|Event|File)>")


class M365CopilotClient:
    name = "m365-copilot"

    def __init__(
        self, api_key: str, model: str = "", base_url: str = DEFAULT_BASE_URL, client: JsonClient | None = None
    ) -> None:
        self.model = model or MODEL_NAME
        self._api_key = api_key
        self._base_url = clean_base_url(base_url)
        self._client = client or JsonClient("Microsoft 365 Copilot", api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        conversation = self._client.post(f"{self._base_url}/copilot/conversations", headers, {})
        conversation_id = conversation.get("id")
        if not conversation_id:
            raise LLMError("Microsoft 365 Copilot did not create a conversation.")

        payload = self._client.post(
            f"{self._base_url}/copilot/conversations/{quote(str(conversation_id), safe='')}/chat",
            headers,
            {
                "message": {"text": _message_text(request)},
                "additionalContext": [{"text": request.context}],
                "locationHint": {"timeZone": "UTC"},
                "contextualResources": {"webContext": {"isWebEnabled": False}},
            },
        )

        messages = payload.get("messages") or []
        if len(messages) < 2:
            raise LLMError("Microsoft 365 Copilot returned no reply.")
        reply = messages[-1]
        _reject_company_data(reply)
        text = _clean_reply(reply.get("text") or "")
        if not text:
            raise LLMError("Microsoft 365 Copilot returned no text.")
        return LLMResponse(text=text, model=self.model)


def _message_text(request: LLMRequest) -> str:
    return (
        f"{request.system}\n\n{request.instructions}\n\n"
        "Answer using only the additional context supplied with this message. Do not "
        "search or use any other documents, emails, chats, or meetings.\n"
        "Reply with one JSON object and nothing else, matching this JSON Schema:\n"
        f"{json.dumps(request.schema)}"
    )


def _reject_company_data(reply: dict[str, Any]) -> None:
    attributions = reply.get("attributions") or []
    if any(item.get("attributionSource") == "grounding" for item in attributions):
        raise LLMPolicyError("Microsoft 365 Copilot used company documents; response discarded.")
    if (reply.get("sensitivityLabel") or {}).get("sensitivityLabelId"):
        raise LLMPolicyError("Microsoft 365 Copilot's reply has a sensitivity label; response discarded.")


def _clean_reply(text: str) -> str:
    """Remove the Markdown fence, citation markers, and entity tags Copilot adds."""
    text = text.strip()
    fenced = _CODE_FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    return _ENTITY_TAG.sub("", _CITATION_MARKER.sub("", text))
