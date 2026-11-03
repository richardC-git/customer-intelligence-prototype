"""Let a language model direct further web searches, without giving it search.

The model never calls the search service. It returns a short plan, and this code
checks each request and runs it. The plan is written from public search results
only, so a page that manipulates the model has no internal data to put in a
query. The company name comes from this code, and requests are limited to a few
plain words on a fixed list of topics, in a fixed number of searches.
"""

import logging
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from sfs_intelligence.llm.base import LLMError
from sfs_intelligence.llm.guard import GuardedLLM, UntrustedDocument, find_injection
from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.research.categories import SearchCategory
from sfs_intelligence.research.fetch import PageCollector
from sfs_intelligence.research.gather import attach_page_text
from sfs_intelligence.research.search import SearchError, SearchProvider

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

MAX_FOLLOW_UP_SEARCHES = 3
MAX_TERM_WORDS = 6
RESULTS_PER_SEARCH = 4

# Letters, digits, and spaces only, so no quotes or operators such as site: or -word.
_PLAIN_TERMS = re.compile(r"[^\W_]+(?: [^\W_]+)*")
_OPERATOR_WORDS = frozenset(("or", "and", "not"))


class SearchFocus(StrEnum):
    PRICING = "pricing"
    CUSTOMERS = "customers"
    SUPPLIERS = "suppliers"
    PARTNERS = "partners"


_FOCUS_LABELS = {
    SearchFocus.PRICING: "Follow-up: pricing",
    SearchFocus.CUSTOMERS: "Follow-up: customers",
    SearchFocus.SUPPLIERS: "Follow-up: suppliers",
    SearchFocus.PARTNERS: "Follow-up: partners",
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class FollowUpSearch(_FrozenModel):
    focus: SearchFocus
    terms: Annotated[str, Field(min_length=1, max_length=60)]


class ResearchPlan(_FrozenModel):
    """What a language model may ask for. It cannot supply a full search query."""

    subject_is_company: bool
    searches: Annotated[tuple[FollowUpSearch, ...], Field(max_length=MAX_FOLLOW_UP_SEARCHES)]


def is_safe_terms(terms: str) -> bool:
    """Return True for a few plain words that cannot act as search operators."""
    words = terms.split()
    return (
        len(words) <= MAX_TERM_WORDS
        and _PLAIN_TERMS.fullmatch(" ".join(words)) is not None
        and not _OPERATOR_WORDS.intersection(word.casefold() for word in words)
        and find_injection(terms) is None
    )


class FollowUpResearch:
    """Run the searches a model asks for and add the results as sources."""

    def __init__(self, search: SearchProvider, pages: PageCollector | None = None) -> None:
        self._search = search
        self._pages = pages

    def extend(
        self, llm: GuardedLLM, company_name: str, sources: Sequence[Source]
    ) -> tuple[tuple[Source, ...], tuple[str, ...]]:
        """Return sources with follow-up results added, plus notes for the user.

        New sources go after internal ones and before other web results, so
        analysis size limits drop general results first. Any failure here keeps
        the sources found so far.
        """
        public = [source for source in sources if source.kind is SourceKind.PUBLIC]
        try:
            plan = llm.analyze(
                "research_plan", company_name, [_to_document(source) for source in public], ResearchPlan
            ).output
        except LLMError as error:
            logger.warning("Follow-up planning failed: %s", type(error).__name__)
            return tuple(sources), ("Follow-up searches were skipped.",)

        safe = [item for item in plan.searches if is_safe_terms(item.terms)]
        notes: list[str] = []
        if len(safe) < len(plan.searches):
            notes.append("A follow-up search requested by the AI was refused.")
        if not plan.subject_is_company or not safe:
            return tuple(sources), tuple(notes)

        found, failed = self._run(company_name, safe, sources)
        if failed:
            notes.append(f"{failed} follow-up search(es) failed.")
        internal = [source for source in sources if source.kind is SourceKind.INTERNAL]
        return (*internal, *found, *public), tuple(notes)

    def _run(
        self, company_name: str, searches: list[FollowUpSearch], sources: Sequence[Source]
    ) -> tuple[list[Source], int]:
        seen = {str(source.url) for source in sources}
        next_id = 1 + max(
            (int(source.id[1:]) for source in sources if source.id[1:].isdecimal()), default=0
        )
        found: list[Source] = []
        failed = 0
        for item in searches:
            category = SearchCategory(
                id=item.focus.value,
                label=_FOCUS_LABELS[item.focus],
                query=f"{{company}} {' '.join(item.terms.split())}",
            )
            try:
                results = self._search.search(
                    category.build_query(company_name), count=RESULTS_PER_SEARCH
                )
            except SearchError:
                failed += 1
                continue
            for result in results:
                if str(result.url) in seen:
                    continue
                seen.add(str(result.url))
                found.append(
                    result.model_copy(update={"id": f"W{next_id}", "category": category.label})
                )
                next_id += 1

        if self._pages and found:
            found, _ = attach_page_text(self._pages, found)
        return found, failed


def _to_document(source: Source) -> UntrustedDocument:
    """Titles and snippets are enough to spot gaps, and cost far less than full pages."""
    return UntrustedDocument(
        id=source.id,
        title=source.title,
        text=source.snippet or "",
        url=str(source.url) if source.url else None,
        category=source.category,
        published=source.published,
    )
