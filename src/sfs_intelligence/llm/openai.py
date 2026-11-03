"""OpenAI and Azure OpenAI adapters for the Chat Completions API.

Chat Completions is used rather than the newer Responses API because Azure and
other OpenAI-compatible services (via ``LLM_BASE_URL``) also provide it.
"""

from sfs_intelligence.llm.base import (
    LLMConfigError,
    LLMError,
    LLMRefusedError,
    LLMRequest,
    LLMResponse,
    LLMTruncatedError,
)
from sfs_intelligence.llm.http import JsonClient, clean_base_url

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIClient:
    name = "openai"

    def __init__(
        self, api_key: str, model: str, base_url: str = DEFAULT_BASE_URL, client: JsonClient | None = None
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = clean_base_url(base_url)
        self._client = client or JsonClient("OpenAI", api_key)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._client.post(
            f"{self._base_url}/chat/completions",
            self._auth_headers(),
            {
                "model": self.model,
                "max_completion_tokens": request.max_output_tokens,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user_message},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "analysis", "strict": True, "schema": request.schema},
                },
            },
        )

        choices = payload.get("choices") or []
        if not choices:
            raise LLMError("The model returned no choices.")
        message = choices[0].get("message") or {}
        finish_reason = choices[0].get("finish_reason")
        if message.get("refusal") or finish_reason == "content_filter":
            raise LLMRefusedError("The model declined the request.")
        if finish_reason == "length":
            raise LLMTruncatedError("The model reached its output limit.")
        if not message.get("content"):
            raise LLMError("The model returned no text.")

        usage = payload.get("usage") or {}
        return LLMResponse(
            text=message["content"],
            model=payload.get("model", self.model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


class AzureOpenAIClient(OpenAIClient):
    """Azure's v1 API. ``model`` is the deployment name."""

    name = "azure"

    def __init__(
        self, api_key: str, model: str, base_url: str = "", client: JsonClient | None = None
    ) -> None:
        if not base_url:
            raise LLMConfigError(
                "LLM_BASE_URL is required for Azure, "
                "e.g. https://YOUR-RESOURCE.openai.azure.com/openai/v1"
            )
        super().__init__(api_key, model, base_url, client or JsonClient("Azure OpenAI", api_key))

    def _auth_headers(self) -> dict[str, str]:
        return {"api-key": self._api_key}
