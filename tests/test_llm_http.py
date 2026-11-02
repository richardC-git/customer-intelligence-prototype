import pytest

from sfs_intelligence.llm import http
from sfs_intelligence.llm.base import LLMConfigError, LLMError
from sfs_intelligence.llm.http import JsonClient, clean_base_url

SECRET = "sk-test-secret-value"


def make_client(sleeps=None, **options) -> JsonClient:
    recorder = sleeps if sleeps is not None else []
    return JsonClient("Test", SECRET, sleep=recorder.append, timeout=5, **options)


def test_posts_json_and_returns_payload(fake_api):
    fake_api.reply({"ok": True})

    assert make_client().post(f"{fake_api.url}/v1", {"x-key": "k"}, {"a": 1}) == {"ok": True}

    request = fake_api.requests[0]
    assert request["body"] == {"a": 1}
    assert request["headers"]["x-key"] == "k"
    assert request["headers"]["content-type"] == "application/json"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retries_temporary_failures(fake_api, status):
    fake_api.reply({"error": {"message": "busy"}}, status)
    fake_api.reply({"ok": True})

    assert make_client().post(fake_api.url, {}, {}) == {"ok": True}
    assert len(fake_api.requests) == 2


def test_gives_up_after_max_retries(fake_api):
    for _ in range(3):
        fake_api.reply({"error": {"message": "still busy"}}, 503)

    with pytest.raises(LLMError, match="HTTP 503: still busy"):
        make_client().post(fake_api.url, {}, {})
    assert len(fake_api.requests) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_does_not_retry_permanent_failures(fake_api, status):
    fake_api.reply({"error": {"message": "nope"}}, status)

    with pytest.raises(LLMError, match=f"HTTP {status}"):
        make_client().post(fake_api.url, {}, {})
    assert len(fake_api.requests) == 1


def test_honours_retry_after_but_caps_the_wait(fake_api):
    fake_api.reply({}, 429, {"Retry-After": "7"})
    fake_api.reply({}, 429, {"Retry-After": "9999"})
    fake_api.reply({"ok": True})
    sleeps = []

    make_client(sleeps).post(fake_api.url, {}, {})

    assert sleeps == [7.0, http.MAX_RETRY_WAIT]


def test_backs_off_without_retry_after(fake_api):
    fake_api.reply({}, 503)
    fake_api.reply({}, 503)
    fake_api.reply({"ok": True})
    sleeps = []

    make_client(sleeps).post(fake_api.url, {}, {})

    assert sleeps == [1.0, 2.0]


def test_retries_then_reports_unreachable_service(fake_api):
    sleeps = []
    with pytest.raises(LLMError, match="Could not reach Test"):
        make_client(sleeps).post("http://127.0.0.1:1/never", {}, {})
    assert len(sleeps) == 2


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_never_follows_redirects_so_keys_cannot_leak(fake_api, status):
    fake_api.reply({}, status, {"Location": f"{fake_api.url}/stolen"})

    with pytest.raises(LLMError, match=f"HTTP {status}"):
        make_client().post(fake_api.url, {"x-api-key": SECRET}, {})

    assert [request["path"] for request in fake_api.requests] == ["/"]


def test_error_messages_never_contain_the_key(fake_api):
    fake_api.reply({"error": {"message": f"bad key {SECRET} given"}}, 400)

    with pytest.raises(LLMError) as caught:
        make_client().post(fake_api.url, {}, {})

    assert SECRET not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


def test_auth_failure_does_not_echo_the_response(fake_api):
    fake_api.reply({"error": {"message": f"key {SECRET} invalid"}}, 401)

    with pytest.raises(LLMError, match="check LLM_API_KEY") as caught:
        make_client().post(fake_api.url, {}, {})

    assert SECRET not in str(caught.value)


def test_long_provider_errors_are_truncated(fake_api):
    fake_api.reply({"error": {"message": "x" * 5000}}, 400)

    with pytest.raises(LLMError) as caught:
        make_client().post(fake_api.url, {}, {})

    assert len(str(caught.value)) < http.MAX_ERROR_CHARS + 50


def test_oversized_response_is_rejected(fake_api, monkeypatch):
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 10)
    fake_api.reply({"text": "x" * 100})

    with pytest.raises(LLMError, match="more than"):
        make_client().post(fake_api.url, {}, {})


@pytest.mark.parametrize("body", [b"<html>not json</html>", b"[1, 2]"])
def test_unusable_response_is_rejected(fake_api, body):
    fake_api.reply(body)

    with pytest.raises(LLMError):
        make_client().post(fake_api.url, {}, {})


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1",
        "http://localhost:11434/v1",
        "http://127.0.0.1:8000",
    ],
)
def test_clean_base_url_accepts_encrypted_or_local(url):
    assert clean_base_url(url + "/") == url


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "http://localhost.evil.com",
        "ftp://example.com",
        "file:///etc/passwd",
        "not a url",
        "https://",
    ],
)
def test_clean_base_url_rejects_everything_else(url):
    with pytest.raises(LLMConfigError):
        clean_base_url(url)
