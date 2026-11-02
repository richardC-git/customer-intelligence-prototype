"""Read a spreadsheet or CSV export into rows keyed by column name."""

import csv
import warnings
from pathlib import Path

from openpyxl import load_workbook


class ImportFileError(ValueError):
    """Raised when an import file cannot be read or has the wrong columns."""


def read_table(path: Path | str, required_columns: tuple[str, ...]) -> list[dict[str, object]]:
    """Return rows from the first sheet of an .xlsx file, or from a .csv file."""
    path = Path(path)
    if not path.is_file():
        raise ImportFileError(f"File not found: {path}")
    match path.suffix.lower():
        case ".xlsx":
            header, rows = _read_xlsx(path)
        case ".csv":
            header, rows = _read_csv(path)
        case _:
            raise ImportFileError("Unsupported file type. Use .xlsx or .csv.")

    missing = [column for column in required_columns if column not in header]
    if missing:
        raise ImportFileError(f"Missing columns: {', '.join(missing)}")
    return [
        dict(zip(header, row))
        for row in rows
        if any(cell not in (None, "") for cell in row)
    ]


def _read_xlsx(path: Path) -> tuple[list[str], list[tuple]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # Excel features openpyxl does not keep.
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
        except Exception as error:
            raise ImportFileError(f"Could not read {path.name}: {error}") from None
        try:
            rows = workbook.worksheets[0].iter_rows(values_only=True)
            header = [str(cell or "").strip() for cell in next(rows, ())]
            return header, list(rows)
        finally:
            workbook.close()


def _read_csv(path: Path) -> tuple[list[str], list[tuple]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
    except (OSError, UnicodeDecodeError) as error:
        raise ImportFileError(f"Could not read {path.name}: {error}") from None
    if not rows:
        return [], []
    return [cell.strip() for cell in rows[0]], [tuple(row) for row in rows[1:]]


def text(value: object) -> str:
    return "" if value is None else str(value).strip()


def number(value: object) -> float:
    """Read a numeric cell; blanks count as zero, anything else invalid is an error."""
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        raise ImportFileError(f"Expected a number, found {value!r}.") from None
