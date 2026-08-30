"""Excel workbook generation for record export.

The workbook is built in memory with openpyxl; nothing is written to disk.
"""

import io
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Font

from app.models.data_record import DataRecord

SHEET_TITLE = "Data records"

COLUMNS: list[tuple[str, str]] = [
    ("ID", "id"),
    ("Title", "title"),
    ("Value", "value"),
    ("Category", "category"),
    ("Timestamp", "timestamp"),
    ("Source", "source"),
    ("Anomaly", "is_anomaly"),
    ("Owner ID", "owner_id"),
    ("Created at", "created_at"),
    ("Updated at", "updated_at"),
]

# Excel treats a leading one of these as the start of a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@")


def neutralise(text: str) -> str:
    """Stop Excel interpreting user text as a formula.

    A leading ' is Excel's own "this is literal text" marker, so the value is
    still read as data rather than evaluated.
    """
    if text and text[0] in FORMULA_PREFIXES:
        return f"'{text}"
    return text


def _cell_value(record: DataRecord, attribute: str) -> Any:
    value = getattr(record, attribute)
    if attribute in {"title", "category"}:
        return neutralise(str(value))
    if attribute == "source":
        return value.value if hasattr(value, "value") else str(value)
    if attribute == "is_anomaly":
        return bool(value)
    return value  # numbers and datetimes stay native Excel types


def build_workbook(records: Iterable[DataRecord]) -> bytes:
    """Return .xlsx bytes with one header row followed by the records."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE

    sheet.append([header for header, _ in COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    for record in records:
        sheet.append([_cell_value(record, attribute) for _, attribute in COLUMNS])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
