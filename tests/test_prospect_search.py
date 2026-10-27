import pytest

from sfs_intelligence.models.prospect import Source, SourceKind
from sfs_intelligence.research.categories import SearchCategory, SearchCategoryConfig
from sfs_intelligence.research.search import SearchError
from sfs_intelligence.workflows.prospect import SearchProspectProvider, run_prospect_profile

CONFIG = SearchCategoryConfig(
    results_per_category=3,
    categories=(
        SearchCategory(id="news", label="News", query="{company} news"),
        SearchCategory(id="hiring", label="Hiring", query="{company} jobs"),
    ),
)


def source(id, url):
    return Source(id=id, title=f"Page {url}", kind=SourceKind.PUBLIC, url=url)


class FakeSearch:
    def __init__(self, results_by_query, errors_by_query=None):
        self.results_by_query = results_by_query
        self.errors_by_query = errors_by_query or {}
        self.calls = []

    def search(self, query, count=10):
        self.calls.append((query, count))
        if query in self.errors_by_query:
            raise self.errors_by_query[query]
        return self.results_by_query.get(query, ())


def test_one_search_per_category_with_tagged_renumbered_sources():
    search = FakeSearch({
        '"Example Co" news': (source("W1", "https://a.com/"), source("W2", "https://b.com/")),
        '"Example Co" jobs': (source("W1", "https://c.com/"),),
    })
    profile = run_prospect_profile("  Example Co ", SearchProspectProvider(search, CONFIG))

    assert search.calls == [('"Example Co" news', 3), ('"Example Co" jobs', 3)]
    assert [(s.id, s.category, str(s.url)) for s in profile.sources] == [
        ("W1", "News", "https://a.com/"),
        ("W2", "News", "https://b.com/"),
        ("W3", "Hiring", "https://c.com/"),
    ]
    assert "3 web results" in profile.summary
    assert "2 search categories" in profile.summary
    assert profile.opportunities == ()
    assert profile.provider == "Web search (FakeSearch)"


def test_duplicate_urls_kept_once_under_first_category():
    search = FakeSearch({
        '"Example Co" news': (source("W1", "https://a.com/"),),
        '"Example Co" jobs': (source("W1", "https://a.com/"), source("W2", "https://c.com/")),
    })
    profile = run_prospect_profile("Example Co", SearchProspectProvider(search, CONFIG))

    assert [(s.id, s.category) for s in profile.sources] == [("W1", "News"), ("W2", "Hiring")]


def test_failed_category_recorded_as_limitation():
    search = FakeSearch(
        {'"Example Co" news': (source("W1", "https://a.com/"),)},
        errors_by_query={'"Example Co" jobs': SearchError("HTTP 429")},
    )
    profile = run_prospect_profile("Example Co", SearchProspectProvider(search, CONFIG))

    assert len(profile.sources) == 1
    assert "1 search categories" in profile.summary
    assert any("Hiring" in item and "429" in item for item in profile.limitations)


def test_all_categories_failing_raises():
    error = SearchError("HTTP 401")
    search = FakeSearch({}, errors_by_query={'"Example Co" news': error, '"Example Co" jobs': error})
    with pytest.raises(SearchError, match="All searches failed"):
        run_prospect_profile("Example Co", SearchProspectProvider(search, CONFIG))


def test_no_results_produces_valid_profile():
    profile = run_prospect_profile("Example Co", SearchProspectProvider(FakeSearch({}), CONFIG))
    assert profile.sources == ()
    assert "No web results" in profile.summary
