"""Import the field sales competitor list (.csv or .xlsx) into the private store.

The field sales PDF shows most competitor names as logos, so its list cannot be
read automatically. It is kept as a table with these columns instead.
"""

from pathlib import Path

from sfs_intelligence.ingestion.tables import ImportFileError, read_table, text
from sfs_intelligence.llm.guard import UnsafeInputError, validate_subject
from sfs_intelligence.models.crm import Competitor
from sfs_intelligence.storage.private import PrivateStore

REQUIRED_COLUMNS = ("Competitor", "Website", "Bus Unit")


def import_competitors(path: Path | str, store: PrivateStore) -> int:
    """Replace the stored competitor list; returns the number imported."""
    competitors: dict[str, Competitor] = {}
    for line, row in enumerate(read_table(path, REQUIRED_COLUMNS), start=2):
        try:
            name = validate_subject(text(row["Competitor"]))
        except UnsafeInputError as error:
            raise ImportFileError(f"Row {line}: {error}") from None
        competitors[name] = Competitor(
            name=name, website=text(row["Website"]), business_unit=text(row["Bus Unit"])
        )

    if not competitors:
        raise ImportFileError("The file has no competitors.")
    store.replace_competitors(competitors.values())
    return len(competitors)
