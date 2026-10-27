from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from sfs_intelligence.models.page import FetchedPage, FetchStatus
from sfs_intelligence.storage import pages
from sfs_intelligence.storage.pages import DuckDBPageStore, page_store_from_env

FETCHED_AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
URL = "https://example.com/news"


def ok_page(url=URL, text="Example Co opens a new plant.", **overrides):
    fields = dict(
        url=url,
        status=FetchStatus.OK,
        fetched_at=FETCHED_AT,
        http_status=200,
        content_type="text/html",
        title="News",
        text=text,
    )
    fields.update(overrides)
    return FetchedPage(**fields)


@pytest.fixture
def store(tmp_path):
    return DuckDBPageStore(tmp_path / "nested" / "cache.duckdb")


def test_save_and_get_round_trip(store):
    page = ok_page()
    store.save([page])

    assert store.get(URL) == page


def test_lookup_normalizes_url(store):
    store.save([ok_page(url="https://Example.com")])

    assert store.get("https://example.com") is not None
    assert set(store.get_many(["https://EXAMPLE.com"])) == {"https://example.com/"}


def test_get_missing_returns_none(store):
    assert store.get(URL) is None


def test_save_replaces_existing_page(store):
    store.save([ok_page(text="Old text.")])
    store.save([ok_page(text="New text.")])

    assert store.get(URL).text == "New text."


def test_get_many_returns_only_stored_pages(store):
    other = "https://example.org/about"
    store.save([ok_page(), ok_page(url=other)])

    found = store.get_many([URL, other, "https://missing.example/"])

    assert set(found) == {URL, other}


def test_fetched_at_converted_to_utc(store):
    pacific = timezone(timedelta(hours=-7))
    store.save([ok_page(fetched_at=datetime(2026, 10, 27, 5, 0, tzinfo=pacific))])

    assert store.get(URL).fetched_at == FETCHED_AT


def test_failed_page_stored_with_error(store):
    page = FetchedPage(url=URL, status=FetchStatus.FAILED, fetched_at=FETCHED_AT, error="HTTP 404")
    store.save([page])

    assert store.get(URL) == page


def test_empty_save_and_lookup_do_not_create_database(tmp_path):
    path = tmp_path / "cache.duckdb"
    store = DuckDBPageStore(path)
    store.save([])

    assert store.get_many([]) == {}
    assert not path.exists()


def test_content_hash_tracks_text():
    assert ok_page(text="A").content_hash == ok_page(text="A").content_hash
    assert ok_page(text="A").content_hash != ok_page(text="B").content_hash


def test_ok_page_requires_text():
    with pytest.raises(ValidationError, match="requires text"):
        ok_page(text=None)


def test_blocked_page_requires_reason():
    with pytest.raises(ValidationError, match="requires an error"):
        FetchedPage(url=URL, status=FetchStatus.BLOCKED, fetched_at=FETCHED_AT)


def test_naive_fetched_at_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        ok_page(fetched_at=datetime(2026, 10, 27, 12, 0))


def test_store_from_env_uses_configured_path(monkeypatch, tmp_path):
    path = tmp_path / "custom.duckdb"
    monkeypatch.setenv(pages.CACHE_PATH_ENV_VAR, str(path))
    page_store_from_env().save([ok_page()])

    assert path.exists()


def test_store_from_env_defaults_to_data_dir(monkeypatch):
    monkeypatch.delenv(pages.CACHE_PATH_ENV_VAR, raising=False)
    assert page_store_from_env()._path == pages.DEFAULT_CACHE_PATH
