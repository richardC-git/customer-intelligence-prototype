"""Prospect profiling boundary with offline and search-backed providers."""

from typing import Protocol

from sfs_intelligence.models.page import FetchStatus
from sfs_intelligence.models.prospect import ProspectProfile, Source
from sfs_intelligence.research.categories import SearchCategoryConfig, load_search_categories
from sfs_intelligence.research.fetch import PageCollector
from sfs_intelligence.research.search import SearchError, SearchProvider


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
    """Run one web search per category and attach the results as sources.

    With a page collector, each source also gets its page's main text.
    Analysis is left to a future LLM provider.
    """

    def __init__(
        self,
        search: SearchProvider,
        config: SearchCategoryConfig | None = None,
        pages: PageCollector | None = None,
    ) -> None:
        self._search = search
        self._config = config or load_search_categories()
        self._pages = pages

    def generate_profile(self, company_name: str) -> ProspectProfile:
        sources: list[Source] = []
        seen_urls: set[str] = set()
        failures: list[str] = []

        for category in self._config.categories:
            try:
                results = self._search.search(
                    category.build_query(company_name), count=self._config.results_per_category
                )
            except SearchError as error:
                failures.append(f"{category.label}: {error}")
                continue
            for result in results:
                if str(result.url) in seen_urls:
                    continue
                seen_urls.add(str(result.url))
                sources.append(
                    result.model_copy(update={"id": f"W{len(sources) + 1}", "category": category.label})
                )

        if len(failures) == len(self._config.categories):
            raise SearchError("All searches failed. " + " ".join(failures))

        missing_text: list[str] = []
        if self._pages and sources:
            sources, missing_text = self._attach_page_text(sources)

        searched = len(self._config.categories) - len(failures)
        summary = (
            f"Found {len(sources)} web results for {company_name} across {searched} "
            "search categories. They have not been analyzed."
            if sources
            else f"No web results were found for {company_name}."
        )
        limitations = [
            "Search results only - no analysis or summarization has been performed.",
            "Results may refer to a different company with a similar name.",
            "No CRM or product data was consulted.",
            "SFS product fit and sales opportunities have not been assessed.",
        ]
        limitations += [f"Search failed for {failure}" for failure in failures]
        if missing_text:
            limitations.append(
                f"Full page text unavailable for {', '.join(missing_text)}; "
                "search snippets only."
            )

        return ProspectProfile(
            company_name=company_name,
            summary=summary,
            sources=tuple(sources),
            limitations=tuple(limitations),
            provider=f"Web search ({type(self._search).__name__})",
        )

    def _attach_page_text(self, sources: list[Source]) -> tuple[list[Source], list[str]]:
        """Return sources with page text added, plus ids of sources without text."""
        pages = self._pages.collect(str(source.url) for source in sources)
        with_text: list[Source] = []
        missing: list[str] = []
        for source in sources:
            page = pages.get(str(source.url))
            if page and page.status is FetchStatus.OK:
                source = source.model_copy(update={"page_text": page.text})
            else:
                missing.append(source.id)
            with_text.append(source)
        return with_text, missing


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
