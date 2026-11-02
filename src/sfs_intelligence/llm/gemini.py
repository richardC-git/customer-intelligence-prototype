"""Google Gemini adapter for the generateContent API."""

from urllib.parse import quote

from sfs_intelligence.llm.base import (
    LLMError,
    LLMRefusedError,
    LLMRequest,
    LLMResponse,
    LLMTruncatedError,
)
from sfs_intelligence.llm.http import JsonClient, clean_base_url

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Every finish reason other than these means Google stopped the response.
_COMPLETED = ("STOP", "FINISH_REASON_UNSPECIFIED", None)


class GeminiClient:
    name = "gemini"

    def __init__(
        self, api_key: str, model: str, base_url: str = DEFAULT_BASE_URL, client: JsonClient | None = None
    ) -> None:
        self.model = model.removeprefix("models/")
        self._api_key = api_key
        self._base_url = clean_base_url(base_url)
        self._client = client or JsonClient("Gemini", api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        # The key goes in a header because URLs end up in logs and error messages.
        payload = self._client.post(
            f"{self._base_url}/models/{quote(self.model, safe='')}:generateContent",
            {"x-goog-api-key": self._api_key},
            {
                "systemInstruction": {"parts": [{"text": request.system}]},
                "contents": [{"role": "user", "parts": [{"text": request.user_message}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": request.schema,
                    "maxOutputTokens": request.max_output_tokens,
                },
            },
        )

        if (payload.get("promptFeedback") or {}).get("blockReason"):
            raise LLMRefusedError("Gemini blocked the request.")
        candidates = payload.get("candidates") or []
        if not candidates:
            raise LLMError("Gemini returned no candidates.")

        finish_reason = candidates[0].get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise LLMTruncatedError("Gemini reached its output limit.")
        if finish_reason not in _COMPLETED:
            raise LLMRefusedError(f"Gemini stopped the response ({finish_reason}).")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if not text:
            raise LLMError("Gemini returned no text.")
        usage = payload.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=payload.get("modelVersion", self.model),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )
