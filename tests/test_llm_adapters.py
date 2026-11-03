"""Adapter contract tests over real HTTP against recorded response shapes.

Requests and responses follow each provider's published API reference. These
prove the adapters speak each format; ``test_llm_live.py`` covers real services.
"""

import pytest

from sfs_intelligence.llm.anthropic import AnthropicClient
from sfs_intelligence.llm.base import (
    LLMConfigError,
    LLMError,
    LLMPolicyError,
    LLMRefusedError,
    LLMRequest,
    LLMTruncatedError,
)
from sfs_intelligence.llm.gemini import GeminiClient
from sfs_intelligence.llm.http import JsonClient
from sfs_intelligence.llm.m365 import M365CopilotClient
from sfs_intelligence.llm.openai import AzureOpenAIClient, OpenAIClient

KEY = "test-key-123"
JSON_REPLY = '{"answer": 1}'
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}
REQUEST = LLMRequest(
    system="SYSTEM POLICY",
    instructions="TASK",
    context="UNTRUSTED CONTEXT",
    schema=SCHEMA,
    max_output_tokens=512,
)


def client():
    return JsonClient("Test", KEY, sleep=lambda _: None, timeout=5)


def anthropic_reply(text=JSON_REPLY, stop_reason="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def openai_reply(text=JSON_REPLY, finish_reason="stop", refusal=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def gemini_reply(text=JSON_REPLY, finish_reason="STOP"):
    return {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish_reason}
        ],
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18},
        "modelVersion": "gemini-test-001",
    }


def m365_reply(text=JSON_REPLY, attributions=(), label=None):
    def message(content, attribution=()):
        return {
            "@odata.type": "#microsoft.graph.copilotConversationResponseMessage",
            "text": content,
            "attributions": list(attribution),
            "sensitivityLabel": {"sensitivityLabelId": label},
        }

    return {"id": "c1", "messages": [message("the prompt echoed"), message(text, attributions)]}


# Anthropic


@pytest.fixture
def anthropic(fake_api):
    return AnthropicClient(KEY, "claude-test", base_url=fake_api.url, client=client())


def test_anthropic_request_shape(anthropic, fake_api):
    fake_api.reply(anthropic_reply())

    anthropic.complete(REQUEST)

    sent = fake_api.requests[0]
    assert sent["path"] == "/messages"
    assert sent["headers"]["x-api-key"] == KEY
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"] == {
        "model": "claude-test",
        "max_tokens": 512,
        "system": "SYSTEM POLICY",
        "messages": [{"role": "user", "content": "TASK\n\nUNTRUSTED CONTEXT"}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }


def test_anthropic_parses_reply_and_usage(anthropic, fake_api):
    fake_api.reply(anthropic_reply())

    response = anthropic.complete(REQUEST)

    assert (response.text, response.model) == (JSON_REPLY, "claude-test")
    assert (response.input_tokens, response.output_tokens) == (11, 7)


def test_anthropic_ignores_non_text_blocks(anthropic, fake_api):
    reply = anthropic_reply()
    reply["content"].insert(0, {"type": "thinking", "thinking": "secret reasoning", "signature": "s"})
    fake_api.reply(reply)

    assert anthropic.complete(REQUEST).text == JSON_REPLY


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (anthropic_reply(stop_reason="refusal"), LLMRefusedError),
        (anthropic_reply(stop_reason="max_tokens"), LLMTruncatedError),
        (anthropic_reply(text=""), LLMError),
    ],
)
def test_anthropic_failures(anthropic, fake_api, reply, error):
    fake_api.reply(reply)

    with pytest.raises(error):
        anthropic.complete(REQUEST)


# OpenAI and Azure


@pytest.fixture
def openai(fake_api):
    return OpenAIClient(KEY, "gpt-test", base_url=fake_api.url, client=client())


def test_openai_request_shape(openai, fake_api):
    fake_api.reply(openai_reply())

    openai.complete(REQUEST)

    sent = fake_api.requests[0]
    assert sent["path"] == "/chat/completions"
    assert sent["headers"]["authorization"] == f"Bearer {KEY}"
    assert sent["body"] == {
        "model": "gpt-test",
        "max_completion_tokens": 512,
        "messages": [
            {"role": "system", "content": "SYSTEM POLICY"},
            {"role": "user", "content": "TASK\n\nUNTRUSTED CONTEXT"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "analysis", "strict": True, "schema": SCHEMA},
        },
    }


def test_openai_parses_reply_and_usage(openai, fake_api):
    fake_api.reply(openai_reply())

    response = openai.complete(REQUEST)

    assert (response.text, response.model) == (JSON_REPLY, "gpt-test")
    assert (response.input_tokens, response.output_tokens) == (11, 7)


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (openai_reply(text=None, refusal="I can't help with that."), LLMRefusedError),
        (openai_reply(finish_reason="content_filter"), LLMRefusedError),
        (openai_reply(finish_reason="length"), LLMTruncatedError),
        (openai_reply(text=""), LLMError),
        ({"choices": []}, LLMError),
    ],
)
def test_openai_failures(openai, fake_api, reply, error):
    fake_api.reply(reply)

    with pytest.raises(error):
        openai.complete(REQUEST)


def test_azure_uses_api_key_header_and_the_deployment_as_model(fake_api):
    azure = AzureOpenAIClient(KEY, "my-deployment", base_url=fake_api.url, client=client())
    fake_api.reply(openai_reply())

    azure.complete(REQUEST)

    sent = fake_api.requests[0]
    assert sent["headers"]["api-key"] == KEY
    assert "authorization" not in sent["headers"]
    assert sent["body"]["model"] == "my-deployment"


def test_azure_requires_a_base_url():
    with pytest.raises(LLMConfigError, match="LLM_BASE_URL"):
        AzureOpenAIClient(KEY, "my-deployment")


# Gemini


@pytest.fixture
def gemini(fake_api):
    return GeminiClient(KEY, "gemini-test", base_url=fake_api.url, client=client())


def test_gemini_request_shape(gemini, fake_api):
    fake_api.reply(gemini_reply())

    gemini.complete(REQUEST)

    sent = fake_api.requests[0]
    assert sent["path"] == "/models/gemini-test:generateContent"
    assert sent["headers"]["x-goog-api-key"] == KEY
    assert KEY not in sent["path"]
    assert sent["body"] == {
        "systemInstruction": {"parts": [{"text": "SYSTEM POLICY"}]},
        "contents": [{"role": "user", "parts": [{"text": "TASK\n\nUNTRUSTED CONTEXT"}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
            "maxOutputTokens": 512,
        },
    }


def test_gemini_parses_reply_and_usage(gemini, fake_api):
    fake_api.reply(gemini_reply())

    response = gemini.complete(REQUEST)

    assert (response.text, response.model) == (JSON_REPLY, "gemini-test-001")
    assert (response.input_tokens, response.output_tokens) == (11, 7)


def test_gemini_skips_thought_parts(gemini, fake_api):
    reply = gemini_reply()
    reply["candidates"][0]["content"]["parts"].insert(0, {"text": "reasoning", "thought": True})
    fake_api.reply(reply)

    assert gemini.complete(REQUEST).text == JSON_REPLY


def test_gemini_accepts_model_names_with_prefix(fake_api):
    gemini = GeminiClient(KEY, "models/gemini-test", base_url=fake_api.url, client=client())
    fake_api.reply(gemini_reply())

    gemini.complete(REQUEST)

    assert fake_api.requests[0]["path"] == "/models/gemini-test:generateContent"


def test_gemini_escapes_model_name_in_the_url(fake_api):
    gemini = GeminiClient(KEY, "../../evil?x=1", base_url=fake_api.url, client=client())
    fake_api.reply(gemini_reply())

    gemini.complete(REQUEST)

    path = fake_api.requests[0]["path"]
    assert path.startswith("/models/") and "/../" not in path and "?" not in path


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, LLMRefusedError),
        (gemini_reply(finish_reason="SAFETY"), LLMRefusedError),
        (gemini_reply(finish_reason="PROHIBITED_CONTENT"), LLMRefusedError),
        (gemini_reply(finish_reason="RECITATION"), LLMRefusedError),
        (gemini_reply(finish_reason="MAX_TOKENS"), LLMTruncatedError),
        ({"candidates": []}, LLMError),
        (gemini_reply(text=""), LLMError),
    ],
)
def test_gemini_failures(gemini, fake_api, reply, error):
    fake_api.reply(reply)

    with pytest.raises(error):
        gemini.complete(REQUEST)


# Microsoft 365 Copilot


@pytest.fixture
def m365(fake_api):
    return M365CopilotClient(KEY, base_url=fake_api.url, client=client())


def script_m365(fake_api, reply):
    fake_api.reply({"id": "c1", "status": "active"}, 201)
    fake_api.reply(reply)


def test_m365_creates_a_new_conversation_then_chats(m365, fake_api):
    script_m365(fake_api, m365_reply())

    m365.complete(REQUEST)

    created, chat = fake_api.requests
    assert created["path"] == "/copilot/conversations"
    assert created["body"] == {}
    assert chat["path"] == "/copilot/conversations/c1/chat"
    assert chat["headers"]["authorization"] == f"Bearer {KEY}"


def test_m365_turns_web_grounding_off_on_every_message(m365, fake_api):
    script_m365(fake_api, m365_reply())

    m365.complete(REQUEST)

    body = fake_api.requests[1]["body"]
    assert body["contextualResources"] == {"webContext": {"isWebEnabled": False}}
    assert body["locationHint"]["timeZone"]


def test_m365_keeps_untrusted_data_out_of_the_instruction_message(m365, fake_api):
    script_m365(fake_api, m365_reply())

    m365.complete(REQUEST)

    body = fake_api.requests[1]["body"]
    assert body["additionalContext"] == [{"text": "UNTRUSTED CONTEXT"}]
    assert "UNTRUSTED CONTEXT" not in body["message"]["text"]
    assert "SYSTEM POLICY" in body["message"]["text"]
    assert '"additionalProperties": false' in body["message"]["text"]


def test_m365_returns_the_reply_not_the_echoed_prompt(m365, fake_api):
    script_m365(fake_api, m365_reply())

    assert m365.complete(REQUEST).text == JSON_REPLY


@pytest.mark.parametrize(
    "text",
    [
        f"```json\n{JSON_REPLY}\n```",
        f"```\n{JSON_REPLY}\n```",
        f"  {JSON_REPLY}\n",
        '{"answer"[^1^]: 1}',
        '{<Person>"answer"</Person>: 1}',
    ],
)
def test_m365_strips_formatting_copilot_adds(m365, fake_api, text):
    script_m365(fake_api, m365_reply(text))

    assert m365.complete(REQUEST).text == JSON_REPLY


def test_m365_rejects_replies_built_on_company_documents(m365, fake_api):
    grounding = {"attributionType": "citation", "attributionSource": "grounding"}
    script_m365(fake_api, m365_reply(attributions=[grounding]))

    with pytest.raises(LLMPolicyError, match="company documents"):
        m365.complete(REQUEST)


def test_m365_accepts_model_annotations(m365, fake_api):
    annotation = {"attributionType": "annotation", "attributionSource": "model"}
    script_m365(fake_api, m365_reply(attributions=[annotation]))

    assert m365.complete(REQUEST).text == JSON_REPLY


def test_m365_rejects_replies_with_a_sensitivity_label(m365, fake_api):
    script_m365(fake_api, m365_reply(label="confidential-label-id"))

    with pytest.raises(LLMPolicyError, match="sensitivity label"):
        m365.complete(REQUEST)


@pytest.mark.parametrize("reply", [{"id": "c1", "messages": []}, m365_reply(text="")])
def test_m365_failures(m365, fake_api, reply):
    script_m365(fake_api, reply)

    with pytest.raises(LLMError):
        m365.complete(REQUEST)


def test_m365_failed_conversation_creation(m365, fake_api):
    fake_api.reply({})

    with pytest.raises(LLMError, match="did not create"):
        m365.complete(REQUEST)


# All HTTP adapters


@pytest.mark.parametrize(
    ("build", "reply"),
    [
        (lambda url: AnthropicClient(KEY, "m", url, client()), anthropic_reply()),
        (lambda url: OpenAIClient(KEY, "m", url, client()), openai_reply()),
        (lambda url: AzureOpenAIClient(KEY, "m", url, client()), openai_reply()),
        (lambda url: GeminiClient(KEY, "m", url, client()), gemini_reply()),
    ],
)
def test_no_adapter_ever_offers_the_model_tools(fake_api, build, reply):
    fake_api.reply(reply)

    build(fake_api.url).complete(REQUEST)

    body = fake_api.requests[0]["body"]
    assert not {"tools", "tool_choice", "functions", "function_call", "toolConfig"} & set(body)


@pytest.mark.parametrize(
    "build",
    [
        lambda url: AnthropicClient(KEY, "m", url),
        lambda url: OpenAIClient(KEY, "m", url),
        lambda url: GeminiClient(KEY, "m", url),
        lambda url: M365CopilotClient(KEY, "", url),
    ],
)
def test_adapters_refuse_to_send_keys_over_plain_http(build):
    with pytest.raises(LLMConfigError, match="https"):
        build("http://api.example.com/v1")
