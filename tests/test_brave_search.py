import io
import json
import urllib.error
from datetime import UTC, datetime

import pytest

from sfs_intelligence.models.prospect import SourceKind
from sfs_intelligence.research import brave
from sfs_intelligence.research.brave import BraveSearchClient, parse_results
from sfs_intelligence.research.search import SearchError

RETRIEVED_AT = datetime(2026, 10, 27, tzinfo=UTC)

PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "Example Co <strong>expands</strong> plant",
                "url": "https://example.com/news",
                "description": "Example Co &amp; partners open a <strong>new</strong> site.",
            },
            {"title": "No URL result", "description": "Skipped."},
            {"title": "Second", "url": "https://example.org/about", "description": ""},
        ]
    }
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_parse_results_builds_public_sources():
    sources = parse_results(PAYLOAD, retrieved_at=RETRIEVED_AT)

    assert [source.id for source in sources] == ["W1", "W2"]
    assert sources[0].title == "Example Co expands plant"
    assert sources[0].snippet == "Example Co & partners open a new site."
    assert str(sources[0].url) == "https://example.com/news"
    assert sources[0].kind is SourceKind.PUBLIC
    assert sources[0].retrieved_at == RETRIEVED_AT
    assert sources[1].snippet is None


def test_parse_results_handles_missing_web_section():
    assert parse_results({}, retrieved_at=RETRIEVED_AT) == ()


def test_search_sends_query_and_key(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["token"] = request.get_header("X-subscription-token")
        return FakeResponse(json.dumps(PAYLOAD).encode())

    monkeypatch.setattr(brave.urllib.request, "urlopen", fake_urlopen)
    sources = BraveSearchClient("test-key").search("Example Co", count=5)

    assert "q=Example+Co" in captured["url"]
    assert "count=5" in captured["url"]
    assert captured["token"] == "test-key"
    assert len(sources) == 2


def test_search_wraps_http_errors_without_leaking_key(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(brave.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SearchError, match="401") as error:
        BraveSearchClient("secret-key").search("Example Co")
    assert "secret-key" not in str(error.value)


@pytest.mark.parametrize("query, count", [("  ", 10), ("Example Co", 0), ("Example Co", 21)])
def test_search_rejects_bad_input(query, count):
    with pytest.raises(ValueError):
        BraveSearchClient("test-key").search(query, count=count)

