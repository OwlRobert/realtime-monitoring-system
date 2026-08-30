import logging
import math
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_read, require_write
from app.crud import audit_log as audit_crud
from app.crud import data_record as record_crud
from app.db.session import get_session
from app.models.audit_log import AuditAction, ResourceType
from app.models.data_record import DataRecord, DataSource
from app.models.user import User, UserRole
from app.schemas.data_record import (
    DataRecordCreate,
    DataRecordPage,
    DataRecordRead,
    DataRecordUpdate,
    ImportResult,
    SortableField,
    SortOrder,
)
from app.services import excel, importer
from app.utils.request_context import client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["records"])

MAX_PAGE_SIZE = 100
# Exports are not paginated, so the cap is explicit and reported in a header.
EXPORT_ROW_LIMIT = 50_000
EXPORT_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
EXPORT_FILENAME = "data_records.xlsx"
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
    request: Request,
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> DataRecordRead:
    record = await record_crud.create(session, payload, owner_id=current_user.id)
    audit_crud.record_event(
        session,
        user_id=current_user.id,
        action=AuditAction.RECORD_CREATE,
        resource_type=ResourceType.RECORD,
        resource_id=record.id,
        detail=f"{record.category}={record.value} source={record.source.value}",
        ip_address=client_ip(request),
    )
    await session.commit()
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


@router.post(
    "/import",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk import records from a CSV or JSON file",
    responses={
        400: {"description": "The file could not be imported."},
        403: {"description": "Viewers may not import."},
    },
)
async def import_records(
    request: Request,
    file: UploadFile = File(description="A .csv or .json file of records."),
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> ImportResult:
    """Validate an entire file, then insert it in a single transaction.

    Imported rows are always `source=IMPORT` and owned by the caller; the
    file cannot influence ownership, provenance or the anomaly flag.
    """
    content = await file.read()
    try:
        rows = importer.parse_upload(file.filename or "", content)
    except importer.ImportError_ as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from None

    # One event for the whole operation, never one per imported row. It is
    # staged before the batch commit so both land together.
    audit_crud.record_event(
        session,
        user_id=current_user.id,
        action=AuditAction.RECORD_IMPORT,
        resource_type=ResourceType.RECORD,
        detail=f"file={file.filename} rows={len(rows)}",
        ip_address=client_ip(request),
    )
    imported = await record_crud.bulk_create(session, rows, owner_id=current_user.id)
    logger.info(
        "User id=%s imported %d records from %s",
        current_user.id,
        imported,
        file.filename,
    )
    return ImportResult(imported=imported, filename=file.filename or "")


@router.get(
    "/export.xlsx",
    summary="Download matching records as an Excel workbook",
    response_class=Response,
    responses={
        200: {
            "content": {EXPORT_MEDIA_TYPE: {}},
            "description": "An .xlsx workbook of the matching records.",
        }
    },
)
async def export_records(
    category: str | None = Query(None, max_length=100),
    source: DataSource | None = Query(None),
    start: datetime | None = Query(None, description="Earliest timestamp, inclusive."),
    end: datetime | None = Query(None, description="Latest timestamp, inclusive."),
    current_user: User = Depends(require_read),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export the same rows `GET /records` would return, without pagination."""
    records = await record_crud.list_for_export(
        session,
        category=category,
        source=source,
        start=start,
        end=end,
        limit=EXPORT_ROW_LIMIT,
    )
    workbook = excel.build_workbook(records)
    truncated = len(records) >= EXPORT_ROW_LIMIT

    logger.info("User id=%s exported %d records", current_user.id, len(records))
    return Response(
        content=workbook,
        media_type=EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"',
            "X-Export-Rows": str(len(records)),
            "X-Export-Row-Limit": str(EXPORT_ROW_LIMIT),
            "X-Export-Truncated": "true" if truncated else "false",
        },
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
    request: Request,
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> DataRecordRead:
    record = await _get_or_404(session, record_id)
    _ensure_may_modify(record, current_user)

    changed = ", ".join(sorted(payload.model_dump(exclude_unset=True)))
    audit_crud.record_event(
        session,
        user_id=current_user.id,
        action=AuditAction.RECORD_UPDATE,
        resource_type=ResourceType.RECORD,
        resource_id=record.id,
        detail=f"fields: {changed}" if changed else "no changes",
        ip_address=client_ip(request),
    )
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
    request: Request,
    current_user: User = Depends(require_write),
    session: AsyncSession = Depends(get_session),
) -> Response:
    record = await _get_or_404(session, record_id)
    _ensure_may_modify(record, current_user)

    audit_crud.record_event(
        session,
        user_id=current_user.id,
        action=AuditAction.RECORD_DELETE,
        resource_type=ResourceType.RECORD,
        resource_id=record.id,
        detail=f"{record.title} ({record.category})",
        ip_address=client_ip(request),
    )
    await record_crud.delete(session, record)
    logger.info("User id=%s deleted record id=%s", current_user.id, record_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
