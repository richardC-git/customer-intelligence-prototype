"""Turn a profile or competitor report into one shareable document.

The verdict and reasons come from the model, but the pricing date warning is
worked out here, so an old price notice is flagged however it was described.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sfs_intelligence.models.competitor import CompetitorReport
from sfs_intelligence.models.crm import CRM_RECORD_ID
from sfs_intelligence.models.prospect import (
    Finding,
    ProspectProfile,
    Recommendation,
    Source,
    SourceKind,
)

MAX_TOP_ACTIONS = 3
MAX_WARNINGS = 4
STALE_AFTER = timedelta(days=730)

NOT_ASSESSED = "Not assessed"
INTERNAL_NOTICE = "SFS internal: contains CRM or field sales data. Do not share outside SFS."
AI_NOTICE = "AI-generated. Check the cited sources before acting on it."
_VERDICTS = {
    Recommendation.PURSUE: "Pursue",
    Recommendation.RESEARCH_FURTHER: "Research further",
    Recommendation.LOW_PRIORITY: "Low priority",
    Recommendation.NOT_ENOUGH_EVIDENCE: "Not enough evidence",
}


@dataclass(frozen=True)
class Section:
    heading: str
    items: tuple[str, ...]
    # Quick-reference sections go in the PDF's narrow column.
    side: bool = False


@dataclass(frozen=True)
class ReportDocument:
    title: str
    subject: str
    generated: date
    provider: str
    verdict: str
    reason: str
    top_actions: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: str
    sections: tuple[Section, ...]
    sources: tuple[Source, ...]
    limitations: tuple[str, ...]
    crm_record: tuple[str, ...] = ()

    @property
    def headline(self) -> str:
        return f"Verdict: {self.verdict}" if self.verdict else "Key takeaway"

    @property
    def is_internal(self) -> bool:
        return any(source.kind is SourceKind.INTERNAL for source in self.sources)


def prospect_document(profile: ProspectProfile, today: date | None = None) -> ReportDocument:
    today = today or date.today()
    reason = profile.recommendation_reason
    sections = (
        Section(
            "Potential opportunities",
            tuple(
                f"{item.description} Why: {item.rationale} [{', '.join(item.source_ids)}]"
                for item in profile.opportunities
            ),
        ),
        Section("Recent developments", _cited(profile.developments)),
        Section(
            "Most similar existing customers",
            tuple(f"{item.name}: {item.similarity_reason}" for item in profile.related_customers),
            side=True,
        ),
        Section("Pricing signals", _cited(profile.pricing_signals), side=True),
        Section("Customers, suppliers & partners", _cited(profile.relationships), side=True),
    )
    return ReportDocument(
        title="Prospect Briefing",
        subject=profile.company_name,
        generated=today,
        provider=profile.provider,
        verdict=_VERDICTS.get(profile.recommendation, NOT_ASSESSED),
        reason=_cite(reason) if reason else "No AI analysis was available for this company.",
        top_actions=tuple(item.description for item in profile.opportunities[:MAX_TOP_ACTIONS]),
        warnings=_warnings(profile.pricing_signals, profile.sources, profile.key_warnings, today),
        summary=profile.summary,
        sections=sections,
        sources=profile.sources,
        limitations=profile.limitations,
        crm_record=_crm_record(profile.sources),
    )


def competitor_document(report: CompetitorReport, today: date | None = None) -> ReportDocument:
    today = today or date.today()
    takeaway = report.key_takeaway
    sections = (
        Section("What this means for SFS", _cited(report.implications)),
        Section("Recent developments", _cited(report.developments)),
        Section("Pricing signals", _cited(report.pricing_signals), side=True),
        Section("Customers, suppliers & partners", _cited(report.relationships), side=True),
    )
    return ReportDocument(
        title="Competitor Briefing",
        subject=report.competitor,
        generated=today,
        provider=report.provider,
        verdict="" if takeaway else NOT_ASSESSED,
        reason=_cite(takeaway) if takeaway else "No AI analysis was available for this competitor.",
        top_actions=tuple(item.statement for item in report.implications[:MAX_TOP_ACTIONS]),
        warnings=_warnings(report.pricing_signals, report.sources, report.key_warnings, today),
        summary=report.summary,
        sections=sections,
        sources=report.sources,
        limitations=report.limitations,
    )


def _crm_record(sources: Sequence[Source]) -> tuple[str, ...]:
    """Return the matched company's CRM lines, without the name the title already shows."""
    for source in sources:
        if source.kind is SourceKind.INTERNAL and source.id == CRM_RECORD_ID and source.page_text:
            lines = source.page_text.splitlines()
            return tuple(line for line in lines if line and not line.startswith("Company:"))
    return ()


def _cite(finding: Finding) -> str:
    return f"{finding.statement} [{', '.join(finding.source_ids)}]"


def _cited(findings: Sequence[Finding]) -> tuple[str, ...]:
    return tuple(_cite(finding) for finding in findings)


def _warnings(
    pricing: Sequence[Finding], sources: Sequence[Source], key: Sequence[str], today: date
) -> tuple[str, ...]:
    age_warning = _pricing_age_warning(pricing, sources, today)
    warnings = (age_warning, *key) if age_warning else tuple(key)
    return warnings[:MAX_WARNINGS]


def _pricing_age_warning(
    pricing: Sequence[Finding], sources: Sequence[Source], today: date
) -> str | None:
    if not pricing:
        return None
    cited = {source_id for finding in pricing for source_id in finding.source_ids}
    dates = [source.published for source in sources if source.id in cited and source.published]
    if not dates:
        return "Pricing evidence has no publication dates, so it may be out of date."
    newest = max(dates)
    if today - newest > STALE_AFTER:
        return f"The newest pricing evidence is from {newest}, so it may be out of date."
    return None
