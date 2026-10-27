import pytest

from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.research.search import SearchError
from sfs_intelligence.workflows.prospect import SearchProspectProvider, run_prospect_profile

RESULTS = (
    Source(id="W1", title="Example Co news", kind=SourceKind.PUBLIC, url="https://example.com/news"),
    Source(id="W2", title="Example Co about", kind=SourceKind.PUBLIC, url="https://example.com/about"),
)


class FakeSearch:
    def __init__(self, results=RESULTS, error=None):
        self.results = results
        self.error = error
        self.calls = []

    def search(self, query, count=10):
        self.calls.append((query, count))
        if self.error:
            raise self.error
        return self.results


def test_profile_contains_search_results_as_sources():
    search = FakeSearch()
    profile = run_prospect_profile("  Example Co ", SearchProspectProvider(search, result_count=5))

    assert search.calls == [("Example Co", 5)]
    assert profile.sources == RESULTS
    assert "2 web results" in profile.summary
    assert profile.opportunities == ()
    assert profile.provider == "Web search (FakeSearch)"
    assert any("no analysis" in limitation for limitation in profile.limitations)


def test_no_results_produces_valid_profile():
    profile = run_prospect_profile("Example Co", SearchProspectProvider(FakeSearch(results=())))
    assert profile.sources == ()
    assert "No web results" in profile.summary


def test_search_errors_propagate():
    provider = SearchProspectProvider(FakeSearch(error=SearchError("HTTP 429")))
    with pytest.raises(SearchError, match="429"):
        run_prospect_profile("Example Co", provider)
