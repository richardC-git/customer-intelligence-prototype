"""Storage for fetched page content, behind a backend-independent interface.

The public cache lives in its own database, separate from private SFS data, so
it can be deleted and rebuilt at any time.
"""

import os
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path
from typing import Protocol

import duckdb
from pydantic import HttpUrl, TypeAdapter

from sfs_intelligence.models.page import FetchedPage

CACHE_PATH_ENV_VAR = "CACHE_DB_PATH"
DEFAULT_CACHE_PATH = Path("data/cache.duckdb")

_URL = TypeAdapter(HttpUrl)

_COLUMNS = (
    "url",
    "status",
    "fetched_at",
    "http_status",
    "content_type",
    "title",
    "text",
    "content_hash",
    "error",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url          VARCHAR PRIMARY KEY,
    status       VARCHAR NOT NULL,
    fetched_at   TIMESTAMP NOT NULL,  -- UTC
    http_status  INTEGER,
    content_type VARCHAR,
    title        VARCHAR,
    text         VARCHAR,
    content_hash VARCHAR,
    error        VARCHAR
)
"""


class PageStore(Protocol):
    """Storage backends implement this contract; pages are keyed by URL."""

    def get(self, url: str) -> FetchedPage | None:
        ...

    def get_many(self, urls: Iterable[str]) -> dict[str, FetchedPage]:
        ...

    def save(self, pages: Iterable[FetchedPage]) -> None:
        ...


class DuckDBPageStore:
    """PageStore backed by a DuckDB file.

    Each call opens and closes its own connection, because DuckDB lets only one
    process hold a database file open for writing at a time.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def get(self, url: str) -> FetchedPage | None:
        return self.get_many([url]).get(normalize_url(url))

    def get_many(self, urls: Iterable[str]) -> dict[str, FetchedPage]:
        """Return stored pages keyed by normalized URL (see ``normalize_url``)."""
        urls = list(dict.fromkeys(normalize_url(url) for url in urls))
        if not urls:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM pages WHERE url IN ?", [urls]
            ).fetchall()
        pages = (_row_to_page(row) for row in rows)
        return {str(page.url): page for page in pages}

    def save(self, pages: Iterable[FetchedPage]) -> None:
        """Insert or replace pages by URL in a single transaction."""
        rows = [_page_to_row(page) for page in pages]
        if not rows:
            return
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with self._connect() as connection:
            connection.executemany(
                f"INSERT OR REPLACE INTO pages ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                rows,
            )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self._path))
        connection.execute(_SCHEMA)
        return connection


def normalize_url(url: str) -> str:
    """Normalize a URL the way stored pages are keyed, e.g. adding a trailing slash."""
    return str(_URL.validate_python(url))


def page_store_from_env() -> PageStore:
    """Open the page cache at CACHE_DB_PATH, or data/cache.duckdb by default."""
    path = os.environ.get(CACHE_PATH_ENV_VAR, "").strip() or DEFAULT_CACHE_PATH
    return DuckDBPageStore(path)


def _page_to_row(page: FetchedPage) -> tuple:
    return (
        str(page.url),
        page.status.value,
        page.fetched_at.astimezone(UTC).replace(tzinfo=None),
        page.http_status,
        page.content_type,
        page.title,
        page.text,
        page.content_hash,
        page.error,
    )


def _row_to_page(row: tuple) -> FetchedPage:
    fields = dict(zip(_COLUMNS, row))
    fields.pop("content_hash")  # Recomputed from text.
    fields["fetched_at"] = fields["fetched_at"].replace(tzinfo=UTC)
    return FetchedPage(**fields)
