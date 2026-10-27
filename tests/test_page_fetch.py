import threading
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message

import pytest

from sfs_intelligence.models.page import FetchedPage, FetchStatus
from sfs_intelligence.research import fetch
from sfs_intelligence.research.fetch import USER_AGENT, HttpPageFetcher, PageCollector
from sfs_intelligence.storage.pages import DuckDBPageStore

ARTICLE = (
    "<html><head><title>Example Co opens plant</title></head><body>"
    "<nav>Home About Contact</nav><article><h1>Example Co opens plant</h1><p>"
    + "Example Co announced a new manufacturing plant that will employ 200 people. " * 5
    + "</p></article><footer>Copyright</footer></body></html>"
).encode()


class FakeResponse:
    def __init__(self, body=b"", status=200, content_type="text/html"):
        self._body = body
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def web(monkeypatch):
    """Route urlopen calls to canned responses keyed by URL; record requests."""
    routes = {}
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, request.get_header("User-agent")))
        response = routes.get(request.full_url)
        if response is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    return routes, requests


def test_extracts_main_text_and_title(web):
    routes, requests = web
    routes["https://example.com/news"] = FakeResponse(ARTICLE)

    page = HttpPageFetcher().fetch("https://example.com/news")

    assert page.status is FetchStatus.OK
    assert "new manufacturing plant" in page.text
    assert "Copyright" not in page.text
    assert page.title == "Example Co opens plant"
    assert page.http_status == 200
    assert all(agent == USER_AGENT for _, agent in requests)


def test_robots_disallow_blocks_without_fetching_page(web):
    routes, requests = web
    routes["https://example.com/robots.txt"] = FakeResponse(
        b"User-agent: *\nDisallow: /private/", content_type="text/plain"
    )
    routes["https://example.com/private/page"] = FakeResponse(ARTICLE)

    page = HttpPageFetcher().fetch("https://example.com/private/page")

    assert page.status is FetchStatus.BLOCKED
    assert "robots.txt" in page.error
    assert [url for url, _ in requests] == ["https://example.com/robots.txt"]


def test_robots_forbidden_blocks_whole_site(web):
    routes, _ = web
    routes["https://example.com/robots.txt"] = urllib.error.HTTPError(
        "https://example.com/robots.txt", 403, "Forbidden", {}, None
    )
    routes["https://example.com/news"] = FakeResponse(ARTICLE)

    assert HttpPageFetcher().fetch("https://example.com/news").status is FetchStatus.BLOCKED


def test_robots_loaded_once_per_site(web):
    routes, requests = web
    routes["https://example.com/a"] = FakeResponse(ARTICLE)
    routes["https://example.com/b"] = FakeResponse(ARTICLE)

    fetcher = HttpPageFetcher()
    fetcher.fetch("https://example.com/a")
    fetcher.fetch("https://example.com/b")

    assert [url for url, _ in requests].count("https://example.com/robots.txt") == 1


@pytest.mark.parametrize(
    "response, status, error",
    [
        (FakeResponse(b"%PDF", content_type="application/pdf"), FetchStatus.FAILED, "content type"),
        (FakeResponse(b"x" * 101), FetchStatus.FAILED, "larger than"),
        (FakeResponse(b"<html><body></body></html>"), FetchStatus.FAILED, "No main text"),
        (
            FakeResponse(b"<html><body><p>Please verify you are human.</p></body></html>"),
            FetchStatus.BLOCKED,
            "CAPTCHA",
        ),
        (
            urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
            FetchStatus.BLOCKED,
            "403",
        ),
        (urllib.error.HTTPError("u", 500, "Server Error", {}, None), FetchStatus.FAILED, "500"),
        (urllib.error.URLError("no route"), FetchStatus.FAILED, "no route"),
    ],
)
def test_unusable_pages_recorded_with_reason(web, response, status, error):
    routes, _ = web
    routes["https://example.com/page"] = response

    page = HttpPageFetcher(max_bytes=100).fetch("https://example.com/page")

    assert page.status is status
    assert error in page.error


class FakeFetcher:
    def __init__(self, status=FetchStatus.OK, delay=0.0):
        self.status = status
        self.delay = delay
        self.fetched = []
        self._active = {}
        self.max_active_per_host = 0
        self._lock = threading.Lock()

    def fetch(self, url):
        host = url.split("/")[2]
        with self._lock:
            self.fetched.append(url)
            self._active[host] = self._active.get(host, 0) + 1
            self.max_active_per_host = max(self.max_active_per_host, self._active[host])
        time.sleep(self.delay)
        with self._lock:
            self._active[host] -= 1
        if self.status is FetchStatus.OK:
            return page(url, text=f"Text of {url}")
        return page(url, status=self.status, error="nope")


def page(url, status=FetchStatus.OK, fetched_at=None, text=None, error=None):
    return FetchedPage(
        url=url,
        status=status,
        fetched_at=fetched_at or datetime.now(UTC),
        text=text,
        error=error,
    )


@pytest.fixture
def store(tmp_path):
    return DuckDBPageStore(tmp_path / "cache.duckdb")


def test_collect_fetches_and_caches(store):
    fetcher = FakeFetcher()
    pages = PageCollector(fetcher, store).collect(["https://a.com/1", "https://b.com/1"])

    assert set(pages) == {"https://a.com/1", "https://b.com/1"}
    assert store.get("https://a.com/1").text == "Text of https://a.com/1"


def test_collect_reuses_fresh_pages_and_refetches_stale_or_failed(store):
    old = datetime.now(UTC) - timedelta(days=30)
    store.save([
        page("https://a.com/fresh", text="Cached"),
        page("https://a.com/stale", fetched_at=old, text="Old"),
        page("https://a.com/failed", status=FetchStatus.FAILED, error="timeout"),
        page("https://a.com/blocked", status=FetchStatus.BLOCKED, error="robots"),
    ])
    fetcher = FakeFetcher()

    pages = PageCollector(fetcher, store, max_age=timedelta(days=7)).collect([
        "https://a.com/fresh", "https://a.com/stale", "https://a.com/failed", "https://a.com/blocked",
    ])

    assert sorted(fetcher.fetched) == ["https://a.com/failed", "https://a.com/stale"]
    assert pages["https://a.com/fresh"].text == "Cached"
    assert pages["https://a.com/blocked"].status is FetchStatus.BLOCKED


def test_collect_fetches_one_page_at_a_time_per_site(store):
    fetcher = FakeFetcher(delay=0.02)
    urls = [f"https://{host}.com/{n}" for host in ("a", "b", "c") for n in range(3)]

    PageCollector(fetcher, store, max_workers=8).collect(urls)

    assert len(fetcher.fetched) == 9
    assert fetcher.max_active_per_host == 1


def test_collect_with_no_urls(store):
    assert PageCollector(FakeFetcher(), store).collect([]) == {}
