"""Brave Search adapter that returns web results as citable sources."""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from pydantic import ValidationError

from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.research.search import SearchError

API_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_COUNT = 20

_TAG = re.compile(r"<[^>]+>")


class BraveSearchClient:
    """SearchProvider backed by the Brave Search web API."""

    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        if not api_key.strip():
            raise SearchError("A Brave Search API key is required.")
        self._api_key = api_key.strip()
        self._timeout = timeout

    def search(self, query: str, count: int = 10) -> tuple[Source, ...]:
        """Return up to ``count`` web results as public sources with ids W1, W2, ..."""
        query = query.strip()
        if not query:
            raise ValueError("A search query is required.")
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count must be between 1 and {MAX_COUNT}.")

        params = urllib.parse.urlencode({"q": query, "count": count})
        request = urllib.request.Request(
            f"{API_URL}?{params}",
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise SearchError(f"Brave Search returned HTTP {error.code}: {error.reason}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise SearchError(f"Could not reach Brave Search: {error}") from None
        except json.JSONDecodeError:
            raise SearchError("Brave Search returned a response that is not JSON.") from None

        return parse_results(payload, retrieved_at=datetime.now(UTC))


def parse_results(payload: dict, retrieved_at: datetime) -> tuple[Source, ...]:
    """Convert a Brave web search response into sources, skipping unusable results."""
    results = (payload.get("web") or {}).get("results") or []
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
            )
        except ValidationError:
            continue
        sources.append(source)
    return tuple(sources)


def _clean(text: str) -> str:
    """Strip Brave's highlight markup (e.g. <strong>) and decode HTML entities."""
    return html.unescape(_TAG.sub("", text)).strip()
