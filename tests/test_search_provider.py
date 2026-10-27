import pytest

from sfs_intelligence.research.brave import BraveSearchClient
from sfs_intelligence.research.search import (
    API_KEY_ENV_VAR,
    PROVIDER_ENV_VAR,
    SearchError,
    search_provider_from_env,
)


def test_brave_selected_case_insensitively(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, " Brave ")
    monkeypatch.setenv(API_KEY_ENV_VAR, "test-key")
    assert isinstance(search_provider_from_env(), BraveSearchClient)


def test_provider_required(monkeypatch):
    monkeypatch.delenv(PROVIDER_ENV_VAR, raising=False)
    with pytest.raises(SearchError, match=PROVIDER_ENV_VAR):
        search_provider_from_env()


def test_unknown_provider_lists_supported(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "bing")
    with pytest.raises(SearchError, match="Supported: brave"):
        search_provider_from_env()


def test_api_key_required(monkeypatch):
    monkeypatch.setenv(PROVIDER_ENV_VAR, "brave")
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    with pytest.raises(SearchError, match=API_KEY_ENV_VAR):
        search_provider_from_env()
