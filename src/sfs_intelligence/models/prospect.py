"""Provider-independent prospect profile data.

Every finding, opportunity, and related customer must cite at least one entry
in the profile's ``sources`` so unsupported AI conclusions fail validation.
"""

from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, HttpUrl, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
SourceIds = Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
Limitation = Annotated[str, Field(min_length=1, max_length=300)]
MAX_LIMITATIONS = 5


def _keep_first_limitations(items: object) -> object:
    """Dropping extra caveats is safe, and models often list too many."""
    return items[:MAX_LIMITATIONS] if isinstance(items, (list, tuple)) else items


Limitations = Annotated[
    tuple[Limitation, ...], BeforeValidator(_keep_first_limitations), Field(max_length=MAX_LIMITATIONS)
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Recommendation(StrEnum):
    PURSUE = "pursue"
    RESEARCH_FURTHER = "research_further"
    LOW_PRIORITY = "low_priority"
    NOT_ENOUGH_EVIDENCE = "not_enough_evidence"


class SourceKind(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


class Source(_FrozenModel):
    """A public page or approved internal record that supports a claim."""

    id: NonEmptyStr
    title: NonEmptyStr
    kind: SourceKind
    url: HttpUrl | None = None
    snippet: str | None = None
    category: str | None = None
    page_text: str | None = None
    retrieved_at: datetime | None = None
    published: date | None = None

    @model_validator(mode="after")
    def _public_sources_need_url(self) -> "Source":
        if self.kind is SourceKind.PUBLIC and self.url is None:
            raise ValueError(f"Public source {self.id!r} requires a URL.")
        return self


class Finding(_FrozenModel):
    """A sourced statement about the company, e.g. a market development."""

    statement: NonEmptyStr
    source_ids: SourceIds


class Opportunity(_FrozenModel):
    description: NonEmptyStr
    rationale: NonEmptyStr
    product_lines: tuple[NonEmptyStr, ...] = ()
    source_ids: SourceIds


class RelatedCustomer(_FrozenModel):
    """An existing SFS customer similar to the prospect."""

    name: NonEmptyStr
    crm_id: str | None = None
    similarity_reason: NonEmptyStr
    source_ids: SourceIds


class ProspectAnalysis(_FrozenModel):
    """What a language model may return. Sources and provider are set by the caller."""

    subject_is_company: bool
    recommendation: Recommendation
    recommendation_reason: Finding
    summary: Annotated[str, Field(min_length=1, max_length=1500)]
    developments: Annotated[tuple[Finding, ...], Field(max_length=10)]
    pricing_signals: Annotated[tuple[Finding, ...], Field(max_length=6)]
    relationships: Annotated[tuple[Finding, ...], Field(max_length=8)]
    opportunities: Annotated[tuple[Opportunity, ...], Field(max_length=8)]
    limitations: Limitations


class ProspectProfile(_FrozenModel):
    company_name: NonEmptyStr
    summary: NonEmptyStr
    recommendation: Recommendation | None = None
    recommendation_reason: Finding | None = None
    key_warnings: tuple[NonEmptyStr, ...] = ()
    developments: tuple[Finding, ...] = ()
    pricing_signals: tuple[Finding, ...] = ()
    relationships: tuple[Finding, ...] = ()
    opportunities: tuple[Opportunity, ...] = ()
    related_customers: tuple[RelatedCustomer, ...] = ()
    sources: tuple[Source, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()
    provider: NonEmptyStr

    @model_validator(mode="after")
    def _citations_resolve(self) -> "ProspectProfile":
        check_citations(
            self.sources,
            (
                *((self.recommendation_reason,) if self.recommendation_reason else ()),
                *self.developments,
                *self.pricing_signals,
                *self.relationships,
                *self.opportunities,
                *self.related_customers,
            ),
        )
        return self


def check_citations(sources: Iterable[Source], items: Iterable[BaseModel]) -> None:
    """Raise unless source ids are unique and every cited id exists."""
    known = [source.id for source in sources]
    duplicates = {source_id for source_id in known if known.count(source_id) > 1}
    if duplicates:
        raise ValueError(f"Duplicate source ids: {sorted(duplicates)}")

    cited = {source_id for item in items for source_id in item.source_ids}
    missing = cited - set(known)
    if missing:
        raise ValueError(f"Cited source ids not in sources: {sorted(missing)}")
