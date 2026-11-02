"""Storage for imported SFS data, kept apart from the public page cache.

Lookups return only the rows a feature asks for. Nothing here lists the CRM in
bulk, so a language model is only ever sent the records for one company.
"""

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import duckdb

from sfs_intelligence.models.crm import Competitor, CrmAccount

PRIVATE_PATH_ENV_VAR = "PRIVATE_DB_PATH"
DEFAULT_PRIVATE_PATH = Path("data/private/sfs.duckdb")

_ACCOUNT_COLUMNS = tuple(CrmAccount.model_fields)
_COMPETITOR_COLUMNS = tuple(Competitor.model_fields)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_accounts (
    company_no         VARCHAR PRIMARY KEY,
    company            VARCHAR NOT NULL,
    name_2             VARCHAR,
    street             VARCHAR,
    postal_code        VARCHAR,
    city               VARCHAR,
    country            VARCHAR,
    region             VARCHAR,
    account_group      VARCHAR,
    sales_office       VARCHAR,
    potential          DOUBLE,
    turnover_12_months DOUBLE
);
CREATE TABLE IF NOT EXISTS competitors (
    name          VARCHAR PRIMARY KEY,
    website       VARCHAR,
    business_unit VARCHAR
);
"""


class PrivateStore(Protocol):
    """Storage backends implement this contract for imported SFS data."""

    def replace_accounts(self, accounts: Iterable[CrmAccount]) -> None:
        ...

    def company_names(self) -> list[str]:
        ...

    def accounts_named(self, company: str) -> list[CrmAccount]:
        ...

    def top_customers(self, sales_offices: Iterable[str], regions: Iterable[str]) -> list[CrmAccount]:
        ...

    def replace_competitors(self, competitors: Iterable[Competitor]) -> None:
        ...

    def competitors(self) -> list[Competitor]:
        ...

    def counts(self) -> dict[str, int]:
        ...


class DuckDBPrivateStore:
    """PrivateStore backed by a DuckDB file; each call uses its own connection."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def replace_accounts(self, accounts: Iterable[CrmAccount]) -> None:
        """Replace the whole CRM table, so an export never leaves stale rows behind."""
        rows = [tuple(getattr(account, column) for column in _ACCOUNT_COLUMNS) for account in accounts]
        self._replace("crm_accounts", _ACCOUNT_COLUMNS, rows)

    def company_names(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT DISTINCT company FROM crm_accounts").fetchall()
        return [row[0] for row in rows]

    def accounts_named(self, company: str) -> list[CrmAccount]:
        return self._accounts("WHERE company = ? ORDER BY company_no", [company])

    def top_customers(self, sales_offices: Iterable[str], regions: Iterable[str]) -> list[CrmAccount]:
        """Return paying customers in the given sales offices or regions, largest first."""
        sales_offices, regions = list(sales_offices), list(regions)
        if not sales_offices and not regions:
            return []
        return self._accounts(
            "WHERE account_group LIKE 'Customer%' AND turnover_12_months > 0 "
            "AND (sales_office IN ? OR region IN ?) ORDER BY turnover_12_months DESC, company_no",
            [sales_offices or [""], regions or [""]],
        )

    def replace_competitors(self, competitors: Iterable[Competitor]) -> None:
        rows = [tuple(getattr(item, column) for column in _COMPETITOR_COLUMNS) for item in competitors]
        self._replace("competitors", _COMPETITOR_COLUMNS, rows)

    def competitors(self) -> list[Competitor]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(_COMPETITOR_COLUMNS)} FROM competitors "
                "ORDER BY business_unit DESC, name"
            ).fetchall()
        return [Competitor(**dict(zip(_COMPETITOR_COLUMNS, row))) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            accounts = connection.execute("SELECT count(*) FROM crm_accounts").fetchone()[0]
            competitors = connection.execute("SELECT count(*) FROM competitors").fetchone()[0]
        return {"crm_accounts": accounts, "competitors": competitors}

    def _accounts(self, condition: str, parameters: list) -> list[CrmAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(_ACCOUNT_COLUMNS)} FROM crm_accounts {condition}", parameters
            ).fetchall()
        return [CrmAccount(**dict(zip(_ACCOUNT_COLUMNS, row))) for row in rows]

    def _replace(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as connection:
            connection.begin()
            connection.execute(f"DELETE FROM {table}")
            if rows:
                connection.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows
                )
            connection.commit()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self._path))
        connection.execute(_SCHEMA)
        return connection


def private_store_from_env() -> PrivateStore:
    """Open the private store at PRIVATE_DB_PATH, or data/private/sfs.duckdb by default."""
    path = os.environ.get(PRIVATE_PATH_ENV_VAR, "").strip() or DEFAULT_PRIVATE_PATH
    return DuckDBPrivateStore(path)
