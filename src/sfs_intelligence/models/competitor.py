"""Provider-independent competitor monitoring data.

Follows the same citation rule as prospect profiles: every statement must cite
at least one entry in the report's ``sources``.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sfs_intelligence.models.prospect import (
    Finding,
    Limitations,
    NonEmptyStr,
    Source,
    check_citations,
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class CompetitorAnalysis(_FrozenModel):
    """What a language model may return. Sources and provider are set by the caller."""

    subject_is_company: bool
    key_takeaway: Finding
    summary: Annotated[str, Field(min_length=1, max_length=1500)]
    developments: Annotated[tuple[Finding, ...], Field(max_length=10)]
    pricing_signals: Annotated[tuple[Finding, ...], Field(max_length=6)]
    relationships: Annotated[tuple[Finding, ...], Field(max_length=8)]
    implications: Annotated[tuple[Finding, ...], Field(max_length=6)]
    limitations: Limitations


class CompetitorReport(_FrozenModel):
    competitor: NonEmptyStr
    business_unit: str = ""
    summary: NonEmptyStr
    key_takeaway: Finding | None = None
    key_warnings: tuple[NonEmptyStr, ...] = ()
    developments: tuple[Finding, ...] = ()
    pricing_signals: tuple[Finding, ...] = ()
    relationships: tuple[Finding, ...] = ()
    implications: tuple[Finding, ...] = ()
    sources: tuple[Source, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()
    provider: NonEmptyStr

    @model_validator(mode="after")
    def _citations_resolve(self) -> "CompetitorReport":
        check_citations(
            self.sources,
            (
                *((self.key_takeaway,) if self.key_takeaway else ()),
                *self.developments,
                *self.pricing_signals,
                *self.relationships,
                *self.implications,
            ),
        )
        return self
