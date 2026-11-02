"""Find a company in the CRM and the existing customers most like it.

This is plain code, not a language model, so results are repeatable and a model
cannot invent a customer relationship or reach records it was not given.
"""

import difflib
import re
from dataclasses import dataclass
from itertools import groupby

from sfs_intelligence.models.crm import CrmAccount
from sfs_intelligence.storage.private import PrivateStore

MAX_SIMILAR_CUSTOMERS = 5
# High enough for typos, low enough not to join different companies with close names.
MATCH_CUTOFF = 0.95

_LEGAL_SUFFIXES = re.compile(
    r"\b(incorporated|inc|llc|l l c|ltd|limited|corporation|corp|company|co|gmbh|ag|plc|lp)\b\.?"
)
_PUNCTUATION = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class CompanySummary:
    """All of a company's CRM rows combined. Street addresses are left out."""

    name: str
    crm_id: str
    is_customer: bool
    locations: int
    regions: tuple[str, ...]
    cities: tuple[str, ...]
    sales_offices: tuple[str, ...]
    potential: float
    turnover_12_months: float


@dataclass(frozen=True)
class SimilarCustomer:
    summary: CompanySummary
    reason: str


def normalize_name(name: str) -> str:
    """Reduce a name to its comparable form, e.g. 'Acme Co., Inc.' becomes 'acme'."""
    cleaned = _PUNCTUATION.sub(" ", name.casefold())
    return " ".join(_LEGAL_SUFFIXES.sub(" ", cleaned).split())


def find_company(store: PrivateStore, name: str) -> CompanySummary | None:
    """Return the single company matching ``name``, tolerating typos but not partial names."""
    by_normalized: dict[str, list[str]] = {}
    for stored in store.company_names():
        by_normalized.setdefault(normalize_name(stored), []).append(stored)

    target = normalize_name(name)
    if not target:
        return None
    close = difflib.get_close_matches(target, by_normalized, n=1, cutoff=MATCH_CUTOFF)
    if not close:
        return None
    accounts = [
        account for stored in by_normalized[close[0]] for account in store.accounts_named(stored)
    ]
    return summarize(accounts)


def is_exact_match(company: CompanySummary, name: str) -> bool:
    return normalize_name(company.name) == normalize_name(name)


def find_similar_customers(store: PrivateStore, company: CompanySummary) -> list[SimilarCustomer]:
    """Rank customers sharing a sales office or region: both first, then by turnover."""
    accounts = store.top_customers(company.sales_offices, company.regions)
    accounts.sort(key=lambda account: account.company)
    summaries = [summarize(list(group)) for _, group in groupby(accounts, key=lambda a: a.company)]

    target = normalize_name(company.name)
    ranked = sorted(
        (summary for summary in summaries if normalize_name(summary.name) != target),
        key=lambda summary: (
            -_shared(summary.sales_offices, company.sales_offices),
            -_shared(summary.regions, company.regions),
            -summary.turnover_12_months,
            summary.name,
        ),
    )
    return [
        SimilarCustomer(summary, _reason(summary, company))
        for summary in ranked[:MAX_SIMILAR_CUSTOMERS]
    ]


def summarize(accounts: list[CrmAccount]) -> CompanySummary:
    return CompanySummary(
        name=accounts[0].company,
        crm_id=accounts[0].company_no,
        is_customer=any(account.is_customer for account in accounts),
        locations=len(accounts),
        regions=_distinct(account.region for account in accounts),
        cities=_distinct(account.city for account in accounts),
        sales_offices=_distinct(account.sales_office for account in accounts),
        potential=sum(account.potential for account in accounts),
        turnover_12_months=sum(account.turnover_12_months for account in accounts),
    )


def describe_company(company: CompanySummary) -> str:
    """Write a company's CRM summary as text for the prompt and the sources list."""
    return "\n".join(
        (
            f"Company: {company.name}",
            f"Status: {'existing customer' if company.is_customer else 'prospect, no sales yet'}",
            f"Locations in CRM: {company.locations}",
            f"Regions: {_listed(company.regions)}",
            f"Cities: {_listed(company.cities[:10])}",
            f"SFS sales offices: {_listed(company.sales_offices)}",
            f"Potential: {company.potential:,.0f}",
            f"Turnover, last 12 months: {company.turnover_12_months:,.2f}",
        )
    )


def describe_similar(customers: list[SimilarCustomer]) -> str:
    return "\n".join(
        f"{customer.summary.name}: {customer.reason}; "
        f"turnover last 12 months {customer.summary.turnover_12_months:,.2f}"
        for customer in customers
    )


def _distinct(values) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))


def _shared(first: tuple[str, ...], second: tuple[str, ...]) -> int:
    return len(set(first) & set(second))


def _reason(customer: CompanySummary, company: CompanySummary) -> str:
    shared = []
    if offices := set(customer.sales_offices) & set(company.sales_offices):
        shared.append(f"sales office {', '.join(sorted(offices))}")
    if regions := set(customer.regions) & set(company.regions):
        shared.append(f"region {', '.join(sorted(regions))}")
    return f"Existing customer sharing {' and '.join(shared)}"


def _listed(values: tuple[str, ...]) -> str:
    return ", ".join(values) or "not recorded"
