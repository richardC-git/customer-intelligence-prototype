"""Prospect profiling boundary with offline and search-backed providers."""

from typing import Protocol

from sfs_intelligence.models.prospect import ProspectProfile
from sfs_intelligence.research.search import SearchProvider


class ProspectProvider(Protocol):
    """Adapters for future research/model services implement this contract."""

    def generate_profile(self, company_name: str) -> ProspectProfile:
        ...


class PlaceholderProspectProvider:
    """Return explicit unknowns without inventing facts about a company."""

    def generate_profile(self, company_name: str) -> ProspectProfile:
        return ProspectProfile(
            company_name=company_name,
            summary="Company research has not been performed.",
            opportunities=(),
            limitations=(
                "PLACEHOLDER ONLY - not researched or verified intelligence.",
                "No external sources, CRM data, or product data were consulted.",
                "SFS product fit and sales opportunities have not been assessed.",
            ),
            provider="Offline placeholder",
        )


class SearchProspectProvider:
    """Attach web search results as sources; analysis is left to a future LLM provider."""

    def __init__(self, search: SearchProvider, result_count: int = 10) -> None:
        self._search = search
        self._result_count = result_count

    def generate_profile(self, company_name: str) -> ProspectProfile:
        sources = self._search.search(company_name, count=self._result_count)
        summary = (
            f"Found {len(sources)} web results for {company_name}. "
            "They have not been analyzed."
            if sources
            else f"No web results were found for {company_name}."
        )
        return ProspectProfile(
            company_name=company_name,
            summary=summary,
            sources=sources,
            limitations=(
                "Search results only - no analysis or summarization has been performed.",
                "Results may refer to a different company with a similar name.",
                "No CRM or product data was consulted.",
                "SFS product fit and sales opportunities have not been assessed.",
            ),
            provider=f"Web search ({type(self._search).__name__})",
        )


def run_prospect_profile(
    company_name: str, provider: ProspectProvider | None = None
) -> ProspectProfile:
    """Validate input before invoking the selected provider; perform no I/O here."""
    company_name = company_name.strip()
    if not company_name:
        raise ValueError("A company name is required.")
    if provider is None:
        provider = PlaceholderProspectProvider()
    return provider.generate_profile(company_name)
