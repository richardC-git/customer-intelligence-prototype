"""Provider-independent prospect profile data.

Every finding, opportunity, and related customer must cite at least one entry
in the profile's ``sources`` so unsupported AI conclusions fail validation.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
SourceIds = Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


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
    retrieved_at: datetime | None = None

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


class ProspectProfile(_FrozenModel):
    company_name: NonEmptyStr
    summary: NonEmptyStr
    developments: tuple[Finding, ...] = ()
    opportunities: tuple[Opportunity, ...] = ()
    related_customers: tuple[RelatedCustomer, ...] = ()
    sources: tuple[Source, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()
    provider: NonEmptyStr

    @model_validator(mode="after")
    def _citations_resolve(self) -> "ProspectProfile":
        known = [source.id for source in self.sources]
        duplicates = {source_id for source_id in known if known.count(source_id) > 1}
        if duplicates:
            raise ValueError(f"Duplicate source ids: {sorted(duplicates)}")

        cited = {
            source_id
            for item in (*self.developments, *self.opportunities, *self.related_customers)
            for source_id in item.source_ids
        }
        missing = cited - set(known)
        if missing:
            raise ValueError(f"Cited source ids not in sources: {sorted(missing)}")
        return self
