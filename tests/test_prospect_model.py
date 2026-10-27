import pytest
from pydantic import ValidationError

from sfs_intelligence.models.prospect import (
    Opportunity,
    ProspectProfile,
    Source,
    SourceKind,
)
from sfs_intelligence.workflows.prospect import run_prospect_profile

PUBLIC_SOURCE = Source(
    id="S1", title="Press release", kind=SourceKind.PUBLIC, url="https://example.com/news"
)


def make_profile(**overrides) -> ProspectProfile:
    fields = dict(
        company_name="Example Co",
        summary="Example summary.",
        opportunities=(
            Opportunity(description="Fasteners", rationale="Expansion", source_ids=("S1",)),
        ),
        sources=(PUBLIC_SOURCE,),
        provider="test",
    )
    fields.update(overrides)
    return ProspectProfile(**fields)


def test_valid_profile_with_citations():
    profile = make_profile()
    assert profile.opportunities[0].source_ids == ("S1",)


def test_unknown_citation_rejected():
    with pytest.raises(ValidationError, match="S2"):
        make_profile(
            opportunities=(
                Opportunity(description="X", rationale="Y", source_ids=("S2",)),
            )
        )


def test_opportunity_requires_a_source():
    with pytest.raises(ValidationError):
        Opportunity(description="X", rationale="Y", source_ids=())


def test_duplicate_source_ids_rejected():
    with pytest.raises(ValidationError, match="Duplicate"):
        make_profile(sources=(PUBLIC_SOURCE, PUBLIC_SOURCE))


def test_public_source_requires_url():
    with pytest.raises(ValidationError, match="URL"):
        Source(id="S1", title="Article", kind=SourceKind.PUBLIC)


def test_internal_source_without_url_allowed():
    source = Source(id="C1", title="CRM export", kind=SourceKind.INTERNAL)
    assert source.url is None


def test_profile_is_frozen():
    profile = make_profile()
    with pytest.raises(ValidationError):
        profile.summary = "changed"


def test_blank_company_name_rejected():
    with pytest.raises(ValidationError):
        make_profile(company_name="   ")


def test_placeholder_workflow_returns_valid_profile():
    profile = run_prospect_profile("  Example Co  ")
    assert profile.company_name == "Example Co"
    assert profile.opportunities == ()
    assert profile.sources == ()
    assert profile.limitations


def test_workflow_rejects_blank_name():
    with pytest.raises(ValueError, match="required"):
        run_prospect_profile("  ")
