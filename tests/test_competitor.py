"""Competitor monitoring tests. All company data here is made up."""

import json

import pytest

from sfs_intelligence.llm.base import LLMError, LLMResponse
from sfs_intelligence.llm.guard import GuardedLLM
from sfs_intelligence.models.competitor import CompetitorAnalysis, CompetitorReport
from sfs_intelligence.models.crm import Competitor
from sfs_intelligence.models.prospect import Finding, Source, SourceKind
from sfs_intelligence.research.categories import COMPETITOR_CATEGORIES_PATH, load_search_categories
from sfs_intelligence.research.search import SearchError, SearchKind
from sfs_intelligence.workflows.competitor import (
    LLMCompetitorProvider,
    SearchCompetitorProvider,
    run_competitor_report,
)

COMPETITOR = Competitor(name="Example Fasteners", website="https://fasteners.example/", business_unit="RC")
UNANALYZED = "Search results only - no analysis or summarization has been performed."


class FakeSearch:
    def __init__(self, results=None, fail=False):
        self.queries = []
        self.results = results
        self.fail = fail

    def search(self, query, count=10, kind=SearchKind.WEB):
        self.queries.append(query)
        if self.fail:
            raise SearchError("down")
        if self.results is not None:
            return self.results
        slug = len(self.queries)
        return (
            Source(
                id="W1",
                title="Example Fasteners raises prices",
                kind=SourceKind.PUBLIC,
                url=f"https://news.example/{slug}",
                snippet="Example Fasteners announced an eight percent price increase on screws.",
            ),
        )


class FakeModel:
    name = "fake"
    model = "m1"

    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(text=self.reply, model=self.model)


def analysis(**overrides) -> str:
    return json.dumps(
        {
            "subject_is_company": True,
            "key_takeaway": {"statement": "Prices are rising.", "source_ids": ["W1"]},
            "summary": "Example Fasteners is raising prices.",
            "developments": [],
            "pricing_signals": [{"statement": "An eight percent price increase.", "source_ids": ["W1"]}],
            "relationships": [],
            "implications": [{"statement": "Reference it with roofing buyers.", "source_ids": ["I1"]}],
            "limitations": [],
        }
        | overrides
    )


def provider(model, search=None, allow=True):
    research = SearchCompetitorProvider(search or FakeSearch())
    return LLMCompetitorProvider(research, GuardedLLM(model, allow_internal_data=allow))


def test_category_config_loads_and_names_the_company():
    config = load_search_categories(COMPETITOR_CATEGORIES_PATH)

    assert len(config.categories) >= 3
    assert any(category.id == "pricing" for category in config.categories)


def test_report_without_a_model_lists_sources_only():
    report = run_competitor_report(COMPETITOR, SearchCompetitorProvider(FakeSearch()))

    assert report.pricing_signals == ()
    assert UNANALYZED in report.limitations
    assert report.sources[0].id == "I1"
    assert report.sources[0].kind is SourceKind.INTERNAL
    assert "Roofing and cladding" in report.sources[0].page_text


def test_searches_use_the_competitor_name_from_the_list():
    search = FakeSearch()

    SearchCompetitorProvider(search).generate_report(COMPETITOR)

    assert all('"Example Fasteners"' in query for query in search.queries)


def test_analysis_is_merged_with_citations():
    report = provider(FakeModel(analysis())).generate_report(COMPETITOR)

    assert report.summary == "Example Fasteners is raising prices."
    assert report.pricing_signals[0].source_ids == ("W1",)
    assert UNANALYZED not in report.limitations
    assert "fake, m1" in report.provider


def test_key_takeaway_reaches_the_report():
    report = provider(FakeModel(analysis())).generate_report(COMPETITOR)

    assert report.key_takeaway.statement == "Prices are rising."


def test_top_limitations_become_key_warnings():
    reply = analysis(limitations=["First gap.", "Second gap.", "Third gap."])

    report = provider(FakeModel(reply)).generate_report(COMPETITOR)

    assert report.key_warnings == ("First gap.", "Second gap.")


def test_field_sales_entry_reaches_the_model():
    model = FakeModel(analysis())

    provider(model).generate_report(COMPETITOR)

    assert "Competes with SFS business unit: Roofing and cladding" in model.requests[0].context


def test_field_sales_data_is_held_back_without_approval():
    model = FakeModel(analysis())

    report = provider(model, allow=False).generate_report(COMPETITOR)

    assert model.requests == []
    assert any("Internal data" in item for item in report.limitations)


def test_model_is_skipped_when_search_finds_nothing():
    model = FakeModel(analysis())

    report = provider(model, FakeSearch(results=())).generate_report(COMPETITOR)

    assert model.requests == []
    assert len(report.sources) == 1


def test_failed_analysis_keeps_the_sources():
    report = provider(FakeModel(LLMError("HTTP 500"))).generate_report(COMPETITOR)

    assert len(report.sources) > 1
    assert "AI analysis unavailable: HTTP 500" in report.limitations[-1]


def test_invented_citation_is_rejected():
    reply = analysis(pricing_signals=[{"statement": "Prices doubled.", "source_ids": ["W99"]}])

    report = provider(FakeModel(reply)).generate_report(COMPETITOR)

    assert report.pricing_signals == ()
    assert "rejected" in report.limitations[-1]


def test_off_topic_report_is_rejected():
    essay = (
        "Preheat the oven, spread tomato sauce across the dough, then scatter mozzarella "
        "and fresh basil before baking."
    )

    report = provider(FakeModel(analysis(summary=essay))).generate_report(COMPETITOR)

    assert "pizza" not in report.summary and "mozzarella" not in report.summary
    assert "unrelated" in report.limitations[-1]


def test_not_about_the_competitor_shows_fixed_text():
    reply = analysis(subject_is_company=False, summary="A long unrelated story.")

    report = provider(FakeModel(reply)).generate_report(COMPETITOR)

    assert report.summary == "No analysis: the sources do not appear to be about Example Fasteners."
    assert report.pricing_signals == ()


def test_all_searches_failing_raises():
    with pytest.raises(SearchError, match="All searches failed"):
        SearchCompetitorProvider(FakeSearch(fail=True)).generate_report(COMPETITOR)


def test_partial_search_failure_is_disclosed():
    class Flaky(FakeSearch):
        def search(self, query, count=10, kind=SearchKind.WEB):
            if "patent" in query or "acquisition" in query:
                raise SearchError("down")
            return super().search(query, count, kind)

    report = SearchCompetitorProvider(Flaky()).generate_report(COMPETITOR)

    assert any("Search failed" in item for item in report.limitations)


def test_report_rejects_citations_to_unknown_sources():
    with pytest.raises(ValueError, match="not in sources"):
        CompetitorReport(
            competitor="Example",
            summary="x",
            developments=(Finding(statement="x", source_ids=("W9",)),),
            provider="test",
        )


def test_analysis_model_cannot_carry_sources_or_urls():
    with pytest.raises(ValueError):
        CompetitorAnalysis.model_validate(json.loads(analysis(url="https://evil.example")))
