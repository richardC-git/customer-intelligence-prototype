"""Provider-agnostic web search boundary.

The search service is chosen by ``SEARCH_PROVIDER`` and authenticated with
``SEARCH_API_KEY``; callers depend only on ``SearchProvider``.
"""

import os
from typing import Protocol

from sfs_intelligence.models.prospect import Source

PROVIDER_ENV_VAR = "SEARCH_PROVIDER"
API_KEY_ENV_VAR = "SEARCH_API_KEY"


class SearchError(RuntimeError):
    """Raised when a search cannot be performed or its response is unusable."""


class SearchProvider(Protocol):
    """Search adapters implement this contract and return results as public sources."""

    def search(self, query: str, count: int = 10) -> tuple[Source, ...]:
        ...


def search_provider_from_env() -> SearchProvider:
    """Build the search provider named by SEARCH_PROVIDER using SEARCH_API_KEY."""
    from sfs_intelligence.research.brave import BraveSearchClient

    providers = {"brave": BraveSearchClient}

    name = os.environ.get(PROVIDER_ENV_VAR, "").strip().lower()
    if not name:
        raise SearchError(f"{PROVIDER_ENV_VAR} is not set. Add it to your .env file.")
    if name not in providers:
        supported = ", ".join(sorted(providers))
        raise SearchError(f"Unknown {PROVIDER_ENV_VAR} {name!r}. Supported: {supported}.")

    api_key = os.environ.get(API_KEY_ENV_VAR, "")
    if not api_key.strip():
        raise SearchError(f"{API_KEY_ENV_VAR} is not set. Add it to your .env file.")
    return providers[name](api_key)
