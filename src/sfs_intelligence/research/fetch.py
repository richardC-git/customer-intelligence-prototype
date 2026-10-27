"""Fetch public web pages and extract their main text.

Pages are fetched only where allowed: robots.txt is respected, the client
identifies itself honestly, and pages behind logins, paywalls, or CAPTCHAs are
recorded as blocked rather than worked around.
"""

import urllib.error
import urllib.request
import urllib.robotparser
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import trafilatura

from sfs_intelligence.models.page import FetchedPage, FetchStatus
from sfs_intelligence.storage.pages import PageStore, normalize_url

USER_AGENT = "SFSCustomerIntelligencePrototype/0.1 (BCIT ISSP research prototype)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
MAX_BYTES = 3_000_000

# Phrases that indicate a bot check rather than real content.
_CHALLENGE_MARKERS = (
    "captcha",
    "verify you are human",
    "are you a robot",
    "checking your browser",
    "enable javascript and cookies to continue",
)


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage:
        ...


class HttpPageFetcher:
    """Fetch a page over HTTP and extract its main text with trafilatura."""

    def __init__(self, timeout: float = 10.0, max_bytes: int = MAX_BYTES) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def fetch(self, url: str) -> FetchedPage:
        if not self._allowed_by_robots(url):
            return _page(url, FetchStatus.BLOCKED, error="Disallowed by robots.txt.")

        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                http_status = response.status
                content_type = response.headers.get_content_type()
                if content_type not in HTML_TYPES:
                    return _page(
                        url, FetchStatus.FAILED, http_status, content_type,
                        error=f"Unsupported content type {content_type}.",
                    )
                body = response.read(self._max_bytes + 1)
        except urllib.error.HTTPError as error:
            status = FetchStatus.BLOCKED if error.code in (401, 402, 403, 407) else FetchStatus.FAILED
            return _page(url, status, error.code, error=f"HTTP {error.code}: {error.reason}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return _page(url, FetchStatus.FAILED, error=f"Could not fetch page: {error}")

        if len(body) > self._max_bytes:
            return _page(
                url, FetchStatus.FAILED, http_status, content_type,
                error=f"Page larger than {self._max_bytes} bytes.",
            )
        return _extract(url, body, http_status, content_type)

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin].can_fetch(USER_AGENT, url)

    def _load_robots(self, origin: str) -> urllib.robotparser.RobotFileParser:
        parser = urllib.robotparser.RobotFileParser()
        request = urllib.request.Request(f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                parser.parse(response.read(500_000).decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError as error:
            # Same convention as urllib.robotparser: access denied means disallow all,
            # any other error (e.g. 404) means there are no rules.
            if error.code in (401, 403):
                parser.disallow_all = True
            else:
                parser.allow_all = True
        except (urllib.error.URLError, TimeoutError, OSError):
            parser.allow_all = True
        return parser


class PageCollector:
    """Return page text for URLs, reusing cached pages and fetching the rest.

    Different sites are fetched in parallel, but pages on the same site are
    fetched one at a time to avoid overloading it.
    """

    def __init__(
        self,
        fetcher: PageFetcher,
        store: PageStore,
        max_age: timedelta = timedelta(days=7),
        max_workers: int = 8,
    ) -> None:
        self._fetcher = fetcher
        self._store = store
        self._max_age = max_age
        self._max_workers = max_workers

    def collect(self, urls: Iterable[str]) -> dict[str, FetchedPage]:
        """Return pages keyed by normalized URL. Failed fetches are retried on each call."""
        urls = list(dict.fromkeys(normalize_url(url) for url in urls))
        cutoff = datetime.now(UTC) - self._max_age
        pages = {
            url: page
            for url, page in self._store.get_many(urls).items()
            if page.status is not FetchStatus.FAILED and page.fetched_at >= cutoff
        }

        to_fetch = [url for url in urls if url not in pages]
        by_host: dict[str, list[str]] = defaultdict(list)
        for url in to_fetch:
            by_host[urlsplit(url).netloc].append(url)

        fetched: list[FetchedPage] = []
        if by_host:
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                for host_pages in executor.map(self._fetch_sequentially, by_host.values()):
                    fetched.extend(host_pages)
        self._store.save(fetched)

        pages.update({str(page.url): page for page in fetched})
        return pages

    def _fetch_sequentially(self, urls: list[str]) -> list[FetchedPage]:
        return [self._fetcher.fetch(url) for url in urls]


def _extract(url: str, body: bytes, http_status: int, content_type: str) -> FetchedPage:
    text = trafilatura.extract(body, url=url, include_comments=False)
    lowered = (text or "").lower()
    if not text or (len(text) < 1000 and any(marker in lowered for marker in _CHALLENGE_MARKERS)):
        is_challenge = any(marker.encode() in body.lower() for marker in _CHALLENGE_MARKERS)
        status = FetchStatus.BLOCKED if is_challenge else FetchStatus.FAILED
        error = "Page shows a bot check (CAPTCHA)." if is_challenge else "No main text found."
        return _page(url, status, http_status, content_type, error=error)

    metadata = trafilatura.extract_metadata(body, default_url=url)
    title = metadata.title if metadata else None
    return _page(url, FetchStatus.OK, http_status, content_type, title=title, text=text)


def _page(
    url: str,
    status: FetchStatus,
    http_status: int | None = None,
    content_type: str | None = None,
    *,
    title: str | None = None,
    text: str | None = None,
    error: str | None = None,
) -> FetchedPage:
    return FetchedPage(
        url=url,
        status=status,
        fetched_at=datetime.now(UTC),
        http_status=http_status,
        content_type=content_type,
        title=title,
        text=text,
        error=error,
    )
