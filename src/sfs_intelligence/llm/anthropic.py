"""Anthropic (Claude) adapter for the Messages API."""

from sfs_intelligence.llm.base import (
    LLMError,
    LLMRefusedError,
    LLMRequest,
    LLMResponse,
    LLMTruncatedError,
)
from sfs_intelligence.llm.http import JsonClient, clean_base_url

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


class AnthropicClient:
    name = "anthropic"

    def __init__(
        self, api_key: str, model: str, base_url: str = DEFAULT_BASE_URL, client: JsonClient | None = None
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = clean_base_url(base_url)
        self._client = client or JsonClient("Anthropic", api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._client.post(
            f"{self._base_url}/messages",
            {"x-api-key": self._api_key, "anthropic-version": API_VERSION},
            {
                "model": self.model,
                "max_tokens": request.max_output_tokens,
                "system": request.system,
                "messages": [{"role": "user", "content": request.user_message}],
                "output_config": {"format": {"type": "json_schema", "schema": request.schema}},
            },
        )

        stop_reason = payload.get("stop_reason")
        if stop_reason == "refusal":
            raise LLMRefusedError("Claude declined the request.")
        if stop_reason == "max_tokens":
            raise LLMTruncatedError("Claude reached its output limit.")

        blocks = payload.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        if not text:
            raise LLMError("Claude returned no text.")
        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text,
            model=payload.get("model", self.model),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
