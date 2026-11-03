"""Prospect profiling boundary with offline, search-backed, and LLM providers."""

from typing import Protocol

from sfs_intelligence.llm.base import LLMError
from sfs_intelligence.llm.guard import (
    AnalysisResult,
    GuardedLLM,
    UntrustedDocument,
    clean_text,
    validate_subject,
)
from sfs_intelligence.matching.crm import (
    describe_company,
    describe_similar,
    find_company,
    find_similar_customers,
    is_exact_match,
)
from sfs_intelligence.models.crm import CRM_RECORD_ID
from sfs_intelligence.models.prospect import (
    ProspectAnalysis,
    ProspectProfile,
    RelatedCustomer,
    Source,
    SourceKind,
)
from sfs_intelligence.research.categories import SearchCategoryConfig, load_search_categories
from sfs_intelligence.research.fetch import PageCollector
from sfs_intelligence.research.follow_up import FollowUpResearch
from sfs_intelligence.research.gather import gather_sources
from sfs_intelligence.research.search import SearchProvider
from sfs_intelligence.storage.private import PrivateStore


# Limitations of raw search results that no longer apply once they are analyzed.
_UNANALYZED_LIMITATIONS = (
    "Search results only - no analysis or summarization has been performed.",
    "SFS product fit and sales opportunities have not been assessed.",
)
_NO_CRM_LIMITATION = "No CRM or product data was consulted."
CRM_NOT_IMPORTED = "CRM data has not been imported. Use Data Management > Import CRM Data."


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
                "No external sources were consulted.",
                _NO_CRM_LIMITATION,
                "SFS product fit and sales opportunities have not been assessed.",
            ),
            provider="Offline placeholder",
        )


class SearchProspectProvider:
    """Run one web search per category and attach the results as sources.

    With a page collector, each source also gets its page's main text.
    Analysis is added by ``LLMProspectProvider``.
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
        gathered = gather_sources(self._search, self._config, self._pages, company_name)

        summary = (
            f"Found {len(gathered.sources)} web results for {company_name} across "
            f"{gathered.searched} search categories. They have not been analyzed."
            if gathered.sources
            else f"No web results were found for {company_name}."
        )
        limitations = [
            *_UNANALYZED_LIMITATIONS,
            "Results may refer to a different company with a similar name.",
            _NO_CRM_LIMITATION,
        ]
        limitations += [f"Search failed for {failure}" for failure in gathered.failures]
        if gathered.missing_text:
            limitations.append(
                f"Full page text unavailable for {', '.join(gathered.missing_text)}; "
                "search snippets only."
            )

        return ProspectProfile(
            company_name=company_name,
            summary=summary,
            sources=gathered.sources,
            limitations=tuple(limitations),
            provider=f"Web search ({type(self._search).__name__})",
        )


class CrmProspectProvider:
    """Add the company's CRM record and its most similar customers to a profile.

    Only the matched company and its similar customers are added, never the rest
    of the CRM. Similar customers are chosen by ``matching.crm``, not by a model.
    """

    def __init__(self, research: ProspectProvider, store: PrivateStore) -> None:
        self._research = research
        self._store = store

    def generate_profile(self, company_name: str) -> ProspectProfile:
        profile = self._research.generate_profile(company_name)
        limitations = tuple(item for item in profile.limitations if item != _NO_CRM_LIMITATION)
        if not self._store.counts()["crm_accounts"]:
            # An empty CRM must not read as "not found", which suggests the company is new.
            return profile.model_copy(
                update={
                    "limitations": (*limitations, CRM_NOT_IMPORTED),
                    "key_warnings": (*profile.key_warnings, CRM_NOT_IMPORTED),
                }
            )
        company = find_company(self._store, company_name)
        if company is None:
            note = "Not found in the CRM, so similar customers could not be identified."
            return profile.model_copy(
                update={
                    "limitations": (*limitations, note),
                    "key_warnings": (*profile.key_warnings, "Not found in the CRM."),
                }
            )

        internal = [_internal_source(CRM_RECORD_ID, "SFS CRM record", describe_company(company))]
        similar = find_similar_customers(self._store, company)
        related: tuple[RelatedCustomer, ...] = ()
        if similar:
            internal.append(_internal_source("I2", "Similar SFS customers", describe_similar(similar)))
            related = tuple(
                RelatedCustomer(
                    name=customer.summary.name,
                    crm_id=customer.summary.crm_id,
                    similarity_reason=customer.reason,
                    source_ids=("I2",),
                )
                for customer in similar
            )
        warnings = profile.key_warnings
        if not is_exact_match(company, company_name):
            note = f"Matched the CRM record named {company.name}. Confirm it is the same company."
            limitations = (*limitations, note)
            warnings = (*warnings, note)
        return profile.model_copy(
            update={
                "sources": (*internal, *profile.sources),  # First, so size limits keep them.
                "related_customers": related,
                "limitations": limitations,
                "key_warnings": warnings,
            }
        )


class LLMProspectProvider:
    """Analyze search results with a language model and cite them in the profile.

    The search results are shown either way, so if the model is unavailable or
    its output is rejected the user still gets them, with a note explaining why.
    """

    def __init__(
        self, research: ProspectProvider, llm: GuardedLLM, follow_up: FollowUpResearch | None = None
    ) -> None:
        self._research = research
        self._llm = llm
        self._follow_up = follow_up

    def generate_profile(self, company_name: str) -> ProspectProfile:
        profile = self._research.generate_profile(company_name)
        if not profile.sources:
            return profile

        if self._follow_up:
            sources, notes = self._follow_up.extend(self._llm, company_name, profile.sources)
            profile = profile.model_copy(
                update={"sources": sources, "limitations": (*profile.limitations, *notes)}
            )

        try:
            result = self._llm.analyze(
                "prospect_profile",
                company_name,
                [_to_document(source) for source in profile.sources],
                ProspectAnalysis,
            )
        except LLMError as error:
            note = f"AI analysis unavailable: {clean_text(str(error))}"
            return profile.model_copy(
                update={
                    "limitations": (*profile.limitations, note),
                    "key_warnings": (note, *profile.key_warnings),
                }
            )
        return self._apply_analysis(profile, result)

    def _apply_analysis(
        self, profile: ProspectProfile, result: AnalysisResult[ProspectAnalysis]
    ) -> ProspectProfile:
        analysis = result.output
        limitations = [
            "AI-generated from the listed sources. Check them before acting on it.",
            *(item for item in profile.limitations if item not in _UNANALYZED_LIMITATIONS),
            *analysis.limitations,
        ]
        warnings = [*profile.key_warnings, *analysis.limitations[:2]]
        if result.withheld_ids:
            note = f"Not analyzed because the text appeared to address an AI: {', '.join(result.withheld_ids)}."
            limitations.append(note)
            warnings.insert(0, note)
        if result.omitted_ids:
            limitations.append(f"Not analyzed due to size limits: {', '.join(result.omitted_ids)}.")

        provider = f"Web search with AI analysis ({self._llm.label})"
        if not analysis.subject_is_company:
            return profile.model_copy(
                update={
                    # Fixed text: nothing a model wrote about a non-company is shown.
                    "summary": f"No analysis: the sources do not show that {profile.company_name} is a company.",
                    "limitations": tuple(limitations),
                    "key_warnings": tuple(warnings),
                    "provider": provider,
                }
            )
        return ProspectProfile(
            company_name=profile.company_name,
            summary=analysis.summary,
            recommendation=analysis.recommendation,
            recommendation_reason=analysis.recommendation_reason,
            key_warnings=tuple(warnings),
            developments=analysis.developments,
            pricing_signals=analysis.pricing_signals,
            relationships=analysis.relationships,
            opportunities=analysis.opportunities,
            related_customers=profile.related_customers,
            sources=profile.sources,
            limitations=tuple(limitations),
            provider=provider,
        )


def _internal_source(source_id: str, title: str, text: str) -> Source:
    return Source(
        id=source_id, title=title, kind=SourceKind.INTERNAL, category="SFS CRM", page_text=text
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


def run_prospect_profile(
    company_name: str, provider: ProspectProvider | None = None
) -> ProspectProfile:
    """Validate input before invoking the selected provider; perform no I/O here."""
    company_name = validate_subject(company_name)
    if provider is None:
        provider = PlaceholderProspectProvider()
    return provider.generate_profile(company_name)
