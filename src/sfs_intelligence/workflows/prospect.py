"""Prospect profiling boundary with an offline default provider."""

from typing import Protocol

from sfs_intelligence.models.prospect import ProspectProfile


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
