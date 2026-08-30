"""Parsing and validation for bulk record import.

Every row is validated before anything is written, so an invalid file leaves
the database untouched. The same Pydantic rules used by the single-record API
are applied here, via DataRecordImportRow.
"""

import csv
import io
import json
from typing import Any

from pydantic import ValidationError

from app.schemas.data_record import DataRecordImportRow

CSV_EXTENSION = ".csv"
JSON_EXTENSION = ".json"
SUPPORTED_EXTENSIONS = (CSV_EXTENSION, JSON_EXTENSION)

REQUIRED_COLUMNS = {"title", "value", "category", "timestamp"}

MAX_IMPORT_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMPORT_RECORDS = 5_000


class ImportError_(Exception):
    """A file that cannot be imported. The message is safe to return."""


def _describe(error: ValidationError, position: int, label: str) -> str:
    first = error.errors()[0]
    field = ".".join(str(part) for part in first["loc"]) or "record"
    return f"{label} {position}: {field} — {first['msg']}"


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportError_("File must be UTF-8 encoded") from None


def parse_csv(content: bytes) -> list[DataRecordImportRow]:
    """Parse a CSV file with a header row. Blank lines are skipped."""
    reader = csv.DictReader(io.StringIO(_decode(content)))

    if reader.fieldnames is None:
        raise ImportError_("CSV file is empty")

    columns = {(name or "").strip() for name in reader.fieldnames}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ImportError_(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}"
        )
    unexpected = columns - REQUIRED_COLUMNS
    if unexpected:
        raise ImportError_(
            f"CSV contains unsupported column(s): {', '.join(sorted(unexpected))}. "
            "Only title, value, category and timestamp may be supplied."
        )

    rows: list[DataRecordImportRow] = []
    for position, raw in enumerate(reader, start=2):  # row 1 is the header
        cleaned = {
            (key or "").strip(): (value.strip() if isinstance(value, str) else value)
            for key, value in raw.items()
            if key is not None
        }
        if not any(cleaned.get(column) for column in REQUIRED_COLUMNS):
            continue  # entirely blank line
        try:
            rows.append(DataRecordImportRow(**cleaned))
        except ValidationError as error:
            raise ImportError_(_describe(error, position, "Row")) from None

    return rows


def parse_json(content: bytes) -> list[DataRecordImportRow]:
    """Parse a JSON file containing a top-level array of objects."""
    try:
        payload: Any = json.loads(_decode(content))
    except json.JSONDecodeError as error:
        raise ImportError_(f"Invalid JSON: {error.msg} (line {error.lineno})") from None

    if not isinstance(payload, list):
        raise ImportError_("JSON must contain a top-level array of records")

    rows: list[DataRecordImportRow] = []
    for position, entry in enumerate(payload, start=1):
        if not isinstance(entry, dict):
            raise ImportError_(f"Record {position}: expected an object")
        try:
            rows.append(DataRecordImportRow(**entry))
        except ValidationError as error:
            raise ImportError_(_describe(error, position, "Record")) from None

    return rows


def parse_upload(filename: str, content: bytes) -> list[DataRecordImportRow]:
    """Validate the whole file and return the rows ready to persist."""
    name = (filename or "").lower()
    if not name.endswith(SUPPORTED_EXTENSIONS):
        raise ImportError_("Only .csv and .json files can be imported")

    if len(content) > MAX_IMPORT_BYTES:
        raise ImportError_(
            f"File is larger than the {MAX_IMPORT_BYTES // (1024 * 1024)} MB import limit"
        )

    rows = parse_csv(content) if name.endswith(CSV_EXTENSION) else parse_json(content)

    if not rows:
        raise ImportError_("File contains no records")
    if len(rows) > MAX_IMPORT_RECORDS:
        raise ImportError_(
            f"File contains {len(rows)} records; the limit is {MAX_IMPORT_RECORDS}"
        )

    return rows
