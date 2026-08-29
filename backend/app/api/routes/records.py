import logging
import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_read, require_write
from app.crud import data_record as record_crud
from app.db.session import get_session
from app.models.data_record import DataRecord, DataSource
from app.models.user import User, UserRole
from app.schemas.data_record import (
    DataRecordCreate,
    DataRecordPage,
    DataRecordRead,
    DataRecordUpdate,
    SortableField,
    SortOrder,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["records"])

MAX_PAGE_SIZE = 100
NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")


async def _get_or_404(session: AsyncSession, record_id: int) -> DataRecord:
    record = await record_crud.get_by_id(session, record_id)
    if record is None:
        raise NOT_FOUND
    return record


def _ensure_may_modify(record: DataRecord, current_user: User) -> None:
    """Admins may modify anything; everyone else only their own records."""
    if current_user.role is UserRole.ADMIN or record.owner_id == current_user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not permitted to modify this record",
    )


@router.post(
    "",
    response_model=DataRecordRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a record",
)
async def create_record(
    payload: DataRecordCreate,
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> DataRecordRead:
    record = await record_crud.create(session, payload, owner_id=current_user.id)
    logger.info("User id=%s created record id=%s", current_user.id, record.id)
    return DataRecordRead.model_validate(record)


@router.get("", response_model=DataRecordPage, summary="List records")
async def list_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    category: str | None = Query(None, max_length=100),
    source: DataSource | None = Query(None),
    start: datetime | None = Query(None, description="Earliest timestamp, inclusive."),
    end: datetime | None = Query(None, description="Latest timestamp, inclusive."),
    sort_by: SortableField = Query(SortableField.TIMESTAMP),
    order: SortOrder = Query(SortOrder.DESC),
    current_user: User = Depends(require_read),
    session: AsyncSession = Depends(get_session),
) -> DataRecordPage:
    """Paginated, filterable, sortable listing. Readable by every role."""
    records, total = await record_crud.list_records(
        session,
        page=page,
        page_size=page_size,
        category=category,
        source=source,
        start=start,
        end=end,
        sort_by=sort_by,
        order=order,
    )
    return DataRecordPage(
        items=[DataRecordRead.model_validate(record) for record in records],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size),
    )


@router.get(
    "/{record_id}",
    response_model=DataRecordRead,
    summary="Read a record",
    responses={404: {"description": "No such record."}},
)
async def read_record(
    record_id: int,
    current_user: User = Depends(require_read),
    session: AsyncSession = Depends(get_session),
) -> DataRecordRead:
    """Reading is not ownership-restricted."""
    return DataRecordRead.model_validate(await _get_or_404(session, record_id))


@router.patch(
    "/{record_id}",
    response_model=DataRecordRead,
    summary="Update a record",
    responses={
        403: {"description": "Not the owner."},
        404: {"description": "No such record."},
    },
)
async def update_record(
    record_id: int,
    payload: DataRecordUpdate,
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> DataRecordRead:
    record = await _get_or_404(session, record_id)
    _ensure_may_modify(record, current_user)

    updated = await record_crud.update(session, record, payload)
    logger.info("User id=%s updated record id=%s", current_user.id, record_id)
    return DataRecordRead.model_validate(updated)


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a record",
    responses={
        403: {"description": "Not the owner."},
        404: {"description": "No such record."},
    },
)
async def delete_record(
    record_id: int,
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> Response:
    record = await _get_or_404(session, record_id)
    _ensure_may_modify(record, current_user)

    await record_crud.delete(session, record)
    logger.info("User id=%s deleted record id=%s", current_user.id, record_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
