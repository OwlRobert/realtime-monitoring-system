from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.data_record import DataRecord, DataSource
from app.schemas.data_record import (
    DataRecordCreate,
    DataRecordRead,
    DataRecordUpdate,
)

TIMESTAMP = datetime(2026, 8, 29, 12, 0, 0)
PAYLOAD = {
    "title": "CPU load",
    "value": 42.5,
    "category": "cpu",
    "timestamp": TIMESTAMP,
}


# --------------------------------------------------------------------------
# Create schema
# --------------------------------------------------------------------------


def test_create_accepts_valid_payload():
    record = DataRecordCreate(**PAYLOAD)

    assert record.title == "CPU load"
    assert record.value == 42.5
    assert record.source is DataSource.MANUAL


@pytest.mark.parametrize("missing", ["title", "value", "category", "timestamp"])
def test_create_requires_every_content_field(missing):
    payload = {key: value for key, value in PAYLOAD.items() if key != missing}

    with pytest.raises(ValidationError):
        DataRecordCreate(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"title": ""},
        {"title": "   "},
        {"category": ""},
        {"title": "x" * 201},
        {"category": "x" * 101},
        {"value": "not-a-number"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"timestamp": "not-a-datetime"},
    ],
)
def test_create_rejects_invalid_values(payload):
    with pytest.raises(ValidationError):
        DataRecordCreate(**{**PAYLOAD, **payload})


def test_create_strips_surrounding_whitespace():
    record = DataRecordCreate(**{**PAYLOAD, "title": "  CPU load  ", "category": " cpu "})

    assert record.title == "CPU load"
    assert record.category == "cpu"


@pytest.mark.parametrize("source", ["MANUAL", "IMPORT"])
def test_create_accepts_client_settable_sources(source):
    assert DataRecordCreate(**PAYLOAD, source=source).source is DataSource(source)


def test_create_rejects_realtime_source():
    """REALTIME marks system-generated data; a client may not claim it."""
    with pytest.raises(ValidationError):
        DataRecordCreate(**PAYLOAD, source="REALTIME")


def test_create_rejects_unknown_source():
    with pytest.raises(ValidationError):
        DataRecordCreate(**PAYLOAD, source="SOMETHING_ELSE")


@pytest.mark.parametrize("field", ["owner_id", "is_anomaly", "id"])
def test_create_rejects_server_controlled_fields(field):
    values = {"owner_id": 1, "is_anomaly": True, "id": 99}

    with pytest.raises(ValidationError):
        DataRecordCreate(**PAYLOAD, **{field: values[field]})

    assert field not in DataRecordCreate.model_fields


# --------------------------------------------------------------------------
# Update schema
# --------------------------------------------------------------------------


def test_update_allows_empty_payload():
    assert DataRecordUpdate().model_dump(exclude_unset=True) == {}


def test_update_is_partial():
    update = DataRecordUpdate(value=99.0)

    assert update.model_dump(exclude_unset=True) == {"value": 99.0}
    assert update.title is None


def test_update_validates_supplied_fields():
    with pytest.raises(ValidationError):
        DataRecordUpdate(title="   ")
    with pytest.raises(ValidationError):
        DataRecordUpdate(value=float("nan"))


@pytest.mark.parametrize("field", ["owner_id", "is_anomaly", "source", "id"])
def test_update_rejects_server_controlled_fields(field):
    values = {"owner_id": 1, "is_anomaly": True, "source": "REALTIME", "id": 99}

    with pytest.raises(ValidationError):
        DataRecordUpdate(**{field: values[field]})

    assert field not in DataRecordUpdate.model_fields


# --------------------------------------------------------------------------
# Read schema / ORM round-trip
# --------------------------------------------------------------------------


async def test_orm_object_serialises_to_read_schema(db_session):
    db_session.add(
        DataRecord(
            title="CPU load",
            value=42.5,
            category="cpu",
            timestamp=TIMESTAMP,
            source=DataSource.MANUAL,
        )
    )
    await db_session.commit()

    record = (await db_session.execute(select(DataRecord))).scalar_one()
    read = DataRecordRead.model_validate(record)

    assert read.id == record.id
    assert read.value == 42.5
    assert read.source is DataSource.MANUAL
    assert read.is_anomaly is False
    assert read.owner_id is None
    assert read.created_at is not None and read.updated_at is not None


async def test_record_can_reference_an_owner(db_session):
    from app.core.security import hash_password
    from app.crud import user as user_crud

    owner = await user_crud.create(
        db_session,
        username="owner",
        email="owner@example.com",
        hashed_password=hash_password("a-strong-password"),
    )
    db_session.add(DataRecord(**PAYLOAD, source=DataSource.MANUAL, owner_id=owner.id))
    await db_session.commit()

    record = (await db_session.execute(select(DataRecord))).scalar_one()
    assert DataRecordRead.model_validate(record).owner_id == owner.id


async def test_realtime_record_needs_no_owner(db_session):
    db_session.add(DataRecord(**PAYLOAD, source=DataSource.REALTIME, is_anomaly=True))
    await db_session.commit()

    record = (await db_session.execute(select(DataRecord))).scalar_one()
    assert record.owner_id is None
    assert record.source is DataSource.REALTIME
    assert record.is_anomaly is True


def test_read_schema_exposes_the_expected_fields():
    assert set(DataRecordRead.model_fields) == {
        "id",
        "title",
        "value",
        "category",
        "timestamp",
        "source",
        "is_anomaly",
        "owner_id",
        "created_at",
        "updated_at",
    }
