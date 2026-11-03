"""Run one web search per category and collect the results as cited sources."""

from dataclasses import dataclass

from sfs_intelligence import progress
from sfs_intelligence.models.page import FetchStatus
from sfs_intelligence.models.prospect import Source
from sfs_intelligence.research.categories import SearchCategoryConfig
from sfs_intelligence.research.fetch import PageCollector
from sfs_intelligence.research.search import SearchError, SearchProvider


@dataclass(frozen=True)
class GatheredSources:
    sources: tuple[Source, ...]
    searched: int
    failures: tuple[str, ...]
    missing_text: tuple[str, ...]


def gather_sources(
    search: SearchProvider,
    config: SearchCategoryConfig,
    pages: PageCollector | None,
    company_name: str,
) -> GatheredSources:
    """Search each category, drop duplicate URLs, and attach page text if possible."""
    sources: list[Source] = []
    seen_urls: set[str] = set()
    failures: list[str] = []

    for number, category in enumerate(config.categories, start=1):
        progress.report(f"searching ({number}/{len(config.categories)})")
        try:
            results = search.search(
                category.build_query(company_name),
                count=config.results_per_category,
                kind=category.kind,
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

    if len(failures) == len(config.categories):
        raise SearchError("All searches failed. " + " ".join(failures))

    missing_text: list[str] = []
    if pages and sources:
        sources, missing_text = attach_page_text(pages, sources)
    return GatheredSources(
        tuple(sources), len(config.categories) - len(failures), tuple(failures), tuple(missing_text)
    )


def attach_page_text(
    collector: PageCollector, sources: list[Source]
) -> tuple[list[Source], list[str]]:
    """Return sources with page text added, plus ids of sources without text."""
    pages = collector.collect(str(source.url) for source in sources)
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
