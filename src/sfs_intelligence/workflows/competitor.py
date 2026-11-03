"""Competitor monitoring boundary with search-backed and LLM providers.

Competitors come only from the imported field sales list, so this feature has
no free-text input at all.
"""

from typing import Protocol

from sfs_intelligence import progress
from sfs_intelligence.llm.base import LLMError
from sfs_intelligence.llm.guard import AnalysisResult, GuardedLLM, UntrustedDocument, clean_text
from sfs_intelligence.models.competitor import CompetitorAnalysis, CompetitorReport
from sfs_intelligence.models.crm import Competitor
from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.research.categories import (
    COMPETITOR_CATEGORIES_PATH,
    SearchCategoryConfig,
    load_search_categories,
)
from sfs_intelligence.research.fetch import PageCollector
from sfs_intelligence.research.follow_up import FollowUpResearch
from sfs_intelligence.research.gather import gather_sources
from sfs_intelligence.research.search import SearchProvider

_UNANALYZED_LIMITATION = "Search results only - no analysis or summarization has been performed."
_BUSINESS_UNITS = {"RC": "Roofing and cladding", "FR": "Flat roof"}


class CompetitorProvider(Protocol):
    def generate_report(self, competitor: Competitor) -> CompetitorReport:
        ...


class SearchCompetitorProvider:
    """Collect recent public sources on a competitor without analyzing them."""

    def __init__(
        self,
        search: SearchProvider,
        config: SearchCategoryConfig | None = None,
        pages: PageCollector | None = None,
    ) -> None:
        self._search = search
        self._config = config or load_search_categories(COMPETITOR_CATEGORIES_PATH)
        self._pages = pages

    def generate_report(self, competitor: Competitor) -> CompetitorReport:
        gathered = gather_sources(self._search, self._config, self._pages, competitor.name)
        limitations = [
            _UNANALYZED_LIMITATION,
            "Results may refer to a different company with a similar name.",
            "The field sales list has no competitor prices, so none are included from it.",
        ]
        limitations += [f"Search failed for {failure}" for failure in gathered.failures]
        if gathered.missing_text:
            limitations.append(
                f"Full page text unavailable for {', '.join(gathered.missing_text)}; "
                "search snippets only."
            )
        return CompetitorReport(
            competitor=competitor.name,
            business_unit=competitor.business_unit,
            summary=f"Found {len(gathered.sources)} web results. They have not been analyzed.",
            sources=(_field_sales_source(competitor), *gathered.sources),
            limitations=tuple(limitations),
            provider=f"Web search ({type(self._search).__name__})",
        )


class LLMCompetitorProvider:
    """Analyze collected sources; if that fails the sources are still returned."""

    def __init__(
        self, research: CompetitorProvider, llm: GuardedLLM, follow_up: FollowUpResearch | None = None
    ) -> None:
        self._research = research
        self._llm = llm
        self._follow_up = follow_up

    def generate_report(self, competitor: Competitor) -> CompetitorReport:
        report = self._research.generate_report(competitor)
        if len(report.sources) < 2:  # Only the field sales entry; nothing to analyze.
            return report

        if self._follow_up:
            sources, notes = self._follow_up.extend(self._llm, competitor.name, report.sources)
            report = report.model_copy(
                update={"sources": sources, "limitations": (*report.limitations, *notes)}
            )

        progress.report("analyzing")
        try:
            result = self._llm.analyze(
                "competitor_monitoring",
                competitor.name,
                [_to_document(source) for source in report.sources],
                CompetitorAnalysis,
            )
        except LLMError as error:
            note = f"AI analysis unavailable: {clean_text(str(error))}"
            return report.model_copy(
                update={"limitations": (*report.limitations, note), "key_warnings": (note,)}
            )
        return self._apply_analysis(report, result)

    def _apply_analysis(
        self, report: CompetitorReport, result: AnalysisResult[CompetitorAnalysis]
    ) -> CompetitorReport:
        analysis = result.output
        provider = f"Web search with AI analysis ({self._llm.label})"
        limitations = [
            "AI-generated from public web pages and the field sales list. "
            "Check the cited sources before acting on it.",
            *(item for item in report.limitations if item != _UNANALYZED_LIMITATION),
            *analysis.limitations,
        ]
        warnings = list(analysis.limitations[:2])
        if result.withheld_ids:
            note = f"Not analyzed because the text appeared to address an AI: {', '.join(result.withheld_ids)}."
            limitations.append(note)
            warnings.insert(0, note)
        if result.omitted_ids:
            limitations.append(f"Not analyzed due to size limits: {', '.join(result.omitted_ids)}.")

        if not analysis.subject_is_company:
            return report.model_copy(
                update={
                    "summary": f"No analysis: the sources do not appear to be about {report.competitor}.",
                    "limitations": tuple(limitations),
                    "key_warnings": tuple(warnings),
                    "provider": provider,
                }
            )
        return report.model_copy(
            update={
                "summary": analysis.summary,
                "key_takeaway": analysis.key_takeaway,
                "key_warnings": tuple(warnings),
                "developments": analysis.developments,
                "pricing_signals": analysis.pricing_signals,
                "relationships": analysis.relationships,
                "implications": analysis.implications,
                "limitations": tuple(limitations),
                "provider": provider,
            }
        )


def _field_sales_source(competitor: Competitor) -> Source:
    business_unit = _BUSINESS_UNITS.get(competitor.business_unit, competitor.business_unit)
    return Source(
        id="I1",
        title="SFS field sales competitor list",
        kind=SourceKind.INTERNAL,
        category="SFS field sales",
        page_text=(
            f"Competitor: {competitor.name}\n"
            f"Competes with SFS business unit: {business_unit or 'not recorded'}\n"
            f"Website: {competitor.website or 'not recorded'}"
        ),
    )


def _to_document(source: Source) -> UntrustedDocument:
    return UntrustedDocument(
        id=source.id,
        title=source.title,
        text=source.page_text or source.snippet or "",
        url=str(source.url) if source.url else None,
        category=source.category,
        internal=source.kind is SourceKind.INTERNAL,
        published=source.published,
    )


def run_competitor_report(competitor: Competitor, provider: CompetitorProvider) -> CompetitorReport:
    return provider.generate_report(competitor)
