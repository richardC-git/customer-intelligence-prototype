import pytest
from pydantic import ValidationError

from sfs_intelligence.research.categories import (
    COMPETITOR_CATEGORIES_PATH,
    DEFAULT_CATEGORIES_PATH,
    SearchCategory,
    SearchCategoryConfig,
    load_search_categories,
)
from sfs_intelligence.research.search import SearchKind

# The public information SFS asked for: website, news, social media, positions, patents.
SFS_CATEGORIES = {"website", "news", "social", "hiring", "patents"}


def test_default_config_is_valid():
    config = load_search_categories()
    assert config.categories
    assert all("{company}" in category.query for category in config.categories)


@pytest.mark.parametrize("path", [DEFAULT_CATEGORIES_PATH, COMPETITOR_CATEGORIES_PATH])
def test_both_features_cover_the_categories_sfs_asked_for(path):
    config = load_search_categories(path)
    ids = {category.id for category in config.categories}
    news = next(category for category in config.categories if category.id == "news")

    assert SFS_CATEGORIES <= ids
    assert news.kind is SearchKind.NEWS


def test_categories_search_the_web_unless_told_otherwise():
    assert SearchCategory(id="x", label="X", query="{company}").kind is SearchKind.WEB


def test_unknown_search_kind_rejected():
    with pytest.raises(ValidationError, match="kind"):
        SearchCategory(id="x", label="X", query="{company}", kind="images")


def test_build_query_quotes_company_and_strips_inner_quotes():
    category = SearchCategory(id="news", label="News", query="{company} news")
    assert category.build_query('  Acme "Tools"  Inc ') == '"Acme Tools Inc" news'


def test_build_query_rejects_blank_name():
    category = SearchCategory(id="news", label="News", query="{company} news")
    with pytest.raises(ValueError, match="required"):
        category.build_query(' " ')


def test_query_must_contain_placeholder():
    with pytest.raises(ValidationError, match="company"):
        SearchCategory(id="news", label="News", query="news")


def test_duplicate_category_ids_rejected():
    category = SearchCategory(id="news", label="News", query="{company} news")
    with pytest.raises(ValidationError, match="Duplicate"):
        SearchCategoryConfig(categories=(category, category))


def test_invalid_file_reports_path(tmp_path):
    path = tmp_path / "categories.toml"
    path.write_text("results_per_category = 5\n")
    with pytest.raises(ValueError, match="categories.toml"):
        load_search_categories(path)
