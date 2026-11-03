"""Import a CRM export (.xlsx or .csv) into the private store.

Only the columns named here are kept; anything else in the export is discarded.
"""

from pathlib import Path

from sfs_intelligence.ingestion.tables import ImportFileError, number, read_table, text
from sfs_intelligence.models.crm import CrmAccount
from sfs_intelligence.storage.private import PrivateStore

_TEXT_COLUMNS = {
    "company_no": "Company No.",
    "company": "Company",
    "name_2": "Name 2",
    "street": "Street",
    "postal_code": "Postal code",
    "city": "City",
    "country": "Country",
    "region": "Region",
    "account_group": "Account Group",
    "sales_office": "Sales Office",
}
_NUMBER_COLUMNS = {"potential": "Potential", "turnover_12_months": "Turnover 12 months"}
REQUIRED_COLUMNS = (*_TEXT_COLUMNS.values(), *_NUMBER_COLUMNS.values())


def import_crm(path: Path | str, store: PrivateStore) -> int:
    """Replace stored CRM data with the file's rows; returns the number imported."""
    rows = read_table(path, REQUIRED_COLUMNS)
    accounts: dict[str, CrmAccount] = {}
    for line, row in enumerate(rows, start=2):
        fields = {name: text(row[column]) for name, column in _TEXT_COLUMNS.items()}
        if not fields["company_no"] or not fields["company"]:
            raise ImportFileError(f"Row {line}: Company No. and Company are required.")
        if fields["company_no"] in accounts:
            raise ImportFileError(f"Row {line}: duplicate Company No. {fields['company_no']}.")
        try:
            numbers = {name: number(row[column]) for name, column in _NUMBER_COLUMNS.items()}
        except ImportFileError as error:
            raise ImportFileError(f"Row {line}: {error}") from None
        accounts[fields["company_no"]] = CrmAccount(**fields, **numbers)

    if not accounts:
        raise ImportFileError("The file has no CRM rows.")
    store.replace_accounts(accounts.values())
    return len(accounts)
