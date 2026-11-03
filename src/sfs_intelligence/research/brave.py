"""Brave Search adapter that returns web or news results as citable sources."""

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime

from pydantic import ValidationError

from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.research.search import SearchError, SearchKind

API_URLS = {
    SearchKind.WEB: "https://api.search.brave.com/res/v1/web/search",
    SearchKind.NEWS: "https://api.search.brave.com/res/v1/news/search",
}
MAX_COUNT = 20

_TAG = re.compile(r"<[^>]+>")


class BraveSearchClient:
    """SearchProvider backed by the Brave Search web and news APIs."""

    def __init__(self, api_key: str, timeout: float = 10.0, min_interval: float = 1.0) -> None:
        """``min_interval`` spaces requests out; Brave's free plan allows ~1 per second."""
        if not api_key.strip():
            raise SearchError("A Brave Search API key is required.")
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request: float | None = None

    def search(
        self, query: str, count: int = 10, kind: SearchKind = SearchKind.WEB
    ) -> tuple[Source, ...]:
        """Return up to ``count`` results as public sources with ids W1, W2, ..."""
        query = query.strip()
        if not query:
            raise ValueError("A search query is required.")
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count must be between 1 and {MAX_COUNT}.")

        params = urllib.parse.urlencode({"q": query, "count": count})
        request = urllib.request.Request(
            f"{API_URLS[kind]}?{params}",
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
        )
        self._wait_for_rate_limit()
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise SearchError(f"Brave Search returned HTTP {error.code}: {error.reason}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise SearchError(f"Could not reach Brave Search: {error}") from None
        except json.JSONDecodeError:
            raise SearchError("Brave Search returned a response that is not JSON.") from None

        return parse_results(payload, retrieved_at=datetime.now(UTC), kind=kind)

    def _wait_for_rate_limit(self) -> None:
        if self._last_request is not None:
            remaining = self._min_interval - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()


def parse_results(
    payload: dict, retrieved_at: datetime, kind: SearchKind = SearchKind.WEB
) -> tuple[Source, ...]:
    """Convert a Brave search response into sources, skipping unusable results."""
    # News responses list results at the top level; web responses nest them under "web".
    container = payload if kind is SearchKind.NEWS else payload.get("web") or {}
    results = container.get("results") or []
    sources: list[Source] = []
    for result in results:
        try:
            source = Source(
                id=f"W{len(sources) + 1}",
                title=_clean(result.get("title", "")),
                kind=SourceKind.PUBLIC,
                url=result.get("url"),
                snippet=_clean(result.get("description", "")) or None,
                retrieved_at=retrieved_at,
                published=_published(result.get("page_age")),
            )
        except ValidationError:
            continue
        sources.append(source)
    return tuple(sources)


def _published(page_age: object) -> date | None:
    """Return the page's date. Many results have none, which must stay unknown."""
    try:
        return date.fromisoformat(str(page_age)[:10])
    except ValueError:
        return None


def _clean(text: str) -> str:
    """Strip Brave's highlight markup (e.g. <strong>) and decode HTML entities."""
    return html.unescape(_TAG.sub("", text)).strip()
