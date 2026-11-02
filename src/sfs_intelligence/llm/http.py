"""JSON-over-HTTPS calls shared by the model adapters.

Credentials are never sent over plain HTTP (except to this machine, for tests
and local models), never follow a redirect to another host, and are removed
from every error message.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from sfs_intelligence.llm.base import LLMConfigError, LLMError

MAX_RESPONSE_BYTES = 5_000_000
MAX_ERROR_CHARS = 300
MAX_RETRY_WAIT = 30.0
RETRYABLE_STATUSES = (408, 429, 500, 502, 503, 504)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would otherwise repeat the request, API key included, at the new host."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def clean_base_url(base_url: str) -> str:
    """Return ``base_url`` without a trailing slash; reject unencrypted remote hosts."""
    parts = urlsplit(base_url)
    if parts.scheme == "https" and parts.hostname:
        return base_url.rstrip("/")
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return base_url.rstrip("/")
    raise LLMConfigError("LLM base URL must use https (http is allowed only for localhost).")


class JsonClient:
    """POST JSON with a timeout, a size limit, and limited retries."""

    def __init__(
        self,
        service: str,
        secret: str,
        timeout: float = 120.0,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._service = service
        self._secret = secret
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep

    def post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json", **headers}
        for attempt in range(self._max_retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with _opener.open(request, timeout=self._timeout) as response:
                    return self._parse(response.read(MAX_RESPONSE_BYTES + 1))
            except urllib.error.HTTPError as error:
                if error.code not in RETRYABLE_STATUSES or attempt == self._max_retries:
                    raise LLMError(self._describe(error)) from None
                self._sleep(_retry_delay(error, attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == self._max_retries:
                    reason = self._redact(str(error))
                    raise LLMError(f"Could not reach {self._service}: {reason}") from None
                self._sleep(_retry_delay(None, attempt))
        raise AssertionError("unreachable")

    def _parse(self, raw: bytes) -> dict[str, Any]:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError(f"{self._service} returned more than {MAX_RESPONSE_BYTES} bytes.")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise LLMError(f"{self._service} returned a response that is not JSON.") from None
        if not isinstance(payload, dict):
            raise LLMError(f"{self._service} returned an unexpected response.")
        return payload

    def _describe(self, error: urllib.error.HTTPError) -> str:
        if error.code in (401, 403):
            detail = "check LLM_API_KEY and that it can use this model"
        else:
            detail = self._redact(_error_detail(error))
        return f"{self._service} returned HTTP {error.code}: {detail}"

    def _redact(self, text: str) -> str:
        return text.replace(self._secret, "[REDACTED]") if self._secret else text


def _error_detail(error: urllib.error.HTTPError) -> str:
    """Return the provider's error message when it sent a readable one."""
    try:
        payload = json.loads(error.read(MAX_RESPONSE_BYTES))
    except (ValueError, OSError):
        return str(error.reason)
    details = payload.get("error") if isinstance(payload, dict) else None
    message = details.get("message") if isinstance(details, dict) else details
    return str(message or error.reason)[:MAX_ERROR_CHARS]


def _retry_delay(error: urllib.error.HTTPError | None, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error else None
    try:
        return min(float(retry_after), MAX_RETRY_WAIT)
    except (TypeError, ValueError):
        return min(2.0**attempt, MAX_RETRY_WAIT)
