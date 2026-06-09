from __future__ import annotations

import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session

from app.api.deps import AuthUserContext, get_current_user
from app.db import get_session
from app.schemas.common import ProductSearchFilters
from app.schemas.inquiry import InquiryParseTaskResult, InquiryRunRequest, InquiryRunTaskResult
from app.services.inquiry.catalog_snap import (
    CatalogSnapCache,
    resolve_catalog_norma,
    snap_inquiry_batch_to_catalog,
)
from app.services.inquiry.file_reader import MAX_INQUIRY_ROWS, read_inquiry_rows_from_bytes
from app.services.inquiry.parser import parse_inquiry_batch
from app.services.inquiry.pipeline import run_inquiry_batch, validate_run_request

router = APIRouter(tags=["inquiries"])

_INQUIRY_PARSE_TASKS: dict[str, dict[str, object]] = {}
_INQUIRY_RUN_TASKS: dict[str, dict[str, object]] = {}
_INQUIRY_PARSE_LOCK = threading.Lock()

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "inquiry_uploads"
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _task_snapshot(task_id: str, *, run: bool = False) -> dict[str, object] | None:
    store = _INQUIRY_RUN_TASKS if run else _INQUIRY_PARSE_TASKS
    with _INQUIRY_PARSE_LOCK:
        task = store.get(task_id)
        return dict(task) if task else None


def _task_update(task_id: str, *, run: bool = False, **patch: object) -> None:
    store = _INQUIRY_RUN_TASKS if run else _INQUIRY_PARSE_TASKS
    with _INQUIRY_PARSE_LOCK:
        task = store.get(task_id)
        if task is None:
            return
        task.update(patch)
        task["updated_at"] = time.time()


def _run_parse_task(task_id: str, file_bytes: bytes, filename: str) -> None:
    _task_update(task_id, state="running")
    try:
        input_rows = read_inquiry_rows_from_bytes(file_bytes, filename=filename)
        if not input_rows:
            raise ValueError("Súbor neobsahuje žiadne riadky s textom položky.")
        total = len(input_rows)
        _task_update(task_id, total_rows=total, rows_scanned=0)

        batch = [(r.row_index, r.raw_text, r.quantity_hint) for r in input_rows]

        def progress(done: int, tot: int) -> None:
            _task_update(task_id, rows_scanned=done, total_rows=tot)

        parsed = parse_inquiry_batch(batch, progress_cb=progress)
        from app.db import engine

        _task_update(task_id, phase="catalog_snap", rows_scanned=total, total_rows=total)

        def snap_progress(done: int, tot: int) -> None:
            _task_update(task_id, phase="catalog_snap", rows_snapped=done, total_rows=tot)

        with Session(engine) as session:
            parsed = snap_inquiry_batch_to_catalog(session, parsed, progress_cb=snap_progress)
        result = InquiryParseTaskResult(
            rows=parsed,
            source_filename=filename,
            total_rows=total,
        )
        _task_update(
            task_id,
            state="done",
            phase="done",
            rows_scanned=total,
            rows_snapped=total,
            total_rows=total,
            result=result.model_dump(mode="json"),
            finished_at=time.time(),
        )
    except Exception as exc:
        _task_update(
            task_id,
            state="error",
            error=str(exc),
            error_code=400,
            finished_at=time.time(),
        )


@router.post("/inquiries/parse/upload")
async def inquiry_parse_upload(
    file: UploadFile = File(...),
    _: AuthUserContext = Depends(get_current_user),
):
    filename = (file.filename or "upload.xlsx").strip()
    low = filename.casefold()
    if not (low.endswith(".xlsx") or low.endswith(".xlsm") or low.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Podporované formáty: .xlsx, .xlsm, .csv")

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Súbor je príliš veľký (max 5 MB).")
    if not data:
        raise HTTPException(status_code=400, detail="Prázdny súbor.")

    try:
        preview = read_inquiry_rows_from_bytes(data, filename=filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not preview:
        raise HTTPException(status_code=400, detail="V súbore nie sú žiadne riadky na parsovanie.")
    if len(preview) > MAX_INQUIRY_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximálne {MAX_INQUIRY_ROWS} riadkov na jeden dopyt.",
        )

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    task_id = uuid4().hex
    dest = _UPLOAD_DIR / f"{task_id}_{filename}"
    dest.write_bytes(data)

    with _INQUIRY_PARSE_LOCK:
        _INQUIRY_PARSE_TASKS[task_id] = {
            "task_id": task_id,
            "state": "queued",
            "rows_scanned": 0,
            "total_rows": len(preview),
            "result": None,
            "error": None,
            "error_code": None,
            "source_filename": filename,
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": None,
        }

    thread = threading.Thread(
        target=_run_parse_task,
        args=(task_id, data, filename),
        name=f"inquiry-parse-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id, "state": "queued", "total_rows": len(preview)}


@router.get("/inquiries/parse/{task_id}")
def inquiry_parse_status(
    task_id: str,
    _: AuthUserContext = Depends(get_current_user),
):
    task = _task_snapshot(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Parse task neexistuje.")

    total_rows = int(task.get("total_rows") or 0)
    rows_scanned = int(task.get("rows_scanned") or 0)
    rows_snapped = int(task.get("rows_snapped") or 0)
    phase = str(task.get("phase") or task.get("state") or "")
    progress_pct = 0
    if total_rows > 0:
        if phase == "catalog_snap":
            progress_pct = min(99, int((rows_snapped / total_rows) * 100))
        else:
            progress_pct = min(100, int((rows_scanned / total_rows) * 100))

    return {
        "task_id": task_id,
        "state": task.get("state"),
        "phase": phase,
        "rows_scanned": rows_scanned,
        "rows_snapped": rows_snapped,
        "total_rows": total_rows,
        "progress_pct": progress_pct,
        "source_filename": task.get("source_filename"),
        "result": task.get("result"),
        "error": task.get("error"),
        "error_code": task.get("error_code"),
    }


@router.post("/inquiries/filter-options/conditional")
def inquiry_filter_options_conditional(
    filters: ProductSearchFilters,
    session: Session = Depends(get_session),
    _: AuthUserContext = Depends(get_current_user),
):
    """Kaskádové možnosti filtrov pre riadok dopytu — rovnaká logika ako vyhľadávanie."""
    from app.api.routes import _build_conditional_filter_options

    if filters.norma:
        filters.norma = resolve_catalog_norma(
            filters.norma,
            known=CatalogSnapCache.load(session).norma_values,
        ) or filters.norma
    return _build_conditional_filter_options(session, filters)


@router.post("/inquiries/parse/preview-row")
def inquiry_parse_preview_row(
    payload: dict[str, object],
    _: AuthUserContext = Depends(get_current_user),
):
    """Jeden riadok — na ladenie promptu bez uploadu."""
    from app.services.inquiry.parser import parse_inquiry_line

    raw = str(payload.get("raw_text") or "").strip()
    row_index = int(payload.get("row_index") or 1)
    parsed = parse_inquiry_line(raw, row_index=row_index)
    return parsed.model_dump(mode="json", by_alias=True)


def _run_inquiry_task(
    task_id: str,
    *,
    rows: list,
    supplier_ids: list[int],
    source_filename: str,
    user_id: int,
) -> None:
    _task_update(task_id, run=True, state="running")
    try:
        from app.db import engine
        from app.schemas.inquiry import InquiryLineParsed

        parsed_rows = [InquiryLineParsed.model_validate(r) for r in rows]
        total = len(parsed_rows)
        _task_update(task_id, run=True, total_rows=total, rows_done=0)

        def progress(done: int, tot: int) -> None:
            _task_update(task_id, run=True, rows_done=done, total_rows=tot)

        with Session(engine) as session:
            result = run_inquiry_batch(
                session,
                rows=parsed_rows,
                supplier_ids=supplier_ids,
                user_id=user_id,
                progress_cb=progress,
            )
        result = result.model_copy(update={"source_filename": source_filename})
        _task_update(
            task_id,
            run=True,
            state="done",
            rows_done=total,
            total_rows=total,
            result=result.model_dump(mode="json"),
            finished_at=time.time(),
        )
    except Exception as exc:
        _task_update(
            task_id,
            run=True,
            state="error",
            error=str(exc),
            error_code=400,
            finished_at=time.time(),
        )


@router.post("/inquiries/run")
async def inquiry_run_start(
    payload: InquiryRunRequest,
    user: AuthUserContext = Depends(get_current_user),
):
    validate_run_request(
        payload.rows,
        payload.supplier_ids,
        ignore_errors=payload.ignore_errors,
    )
    if len(payload.rows) > MAX_INQUIRY_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximálne {MAX_INQUIRY_ROWS} riadkov na jeden dopyt.",
        )

    task_id = uuid4().hex
    rows_json = [r.model_dump(mode="json") for r in payload.rows]
    with _INQUIRY_PARSE_LOCK:
        _INQUIRY_RUN_TASKS[task_id] = {
            "task_id": task_id,
            "state": "queued",
            "rows_done": 0,
            "total_rows": len(payload.rows),
            "result": None,
            "error": None,
            "error_code": None,
            "source_filename": payload.source_filename,
            "created_at": time.time(),
            "updated_at": time.time(),
            "finished_at": None,
        }

    thread = threading.Thread(
        target=_run_inquiry_task,
        kwargs={
            "task_id": task_id,
            "rows": rows_json,
            "supplier_ids": payload.supplier_ids,
            "source_filename": payload.source_filename,
            "user_id": user.id,
        },
        name=f"inquiry-run-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {
        "task_id": task_id,
        "state": "queued",
        "total_rows": len(payload.rows),
    }


@router.get("/inquiries/run/{task_id}")
def inquiry_run_status(
    task_id: str,
    _: AuthUserContext = Depends(get_current_user),
):
    task = _task_snapshot(task_id, run=True)
    if task is None:
        raise HTTPException(status_code=404, detail="Run task neexistuje.")

    total_rows = int(task.get("total_rows") or 0)
    rows_done = int(task.get("rows_done") or 0)
    progress_pct = 0
    if total_rows > 0:
        progress_pct = min(100, int((rows_done / total_rows) * 100))

    return {
        "task_id": task_id,
        "state": task.get("state"),
        "rows_done": rows_done,
        "total_rows": total_rows,
        "progress_pct": progress_pct,
        "source_filename": task.get("source_filename"),
        "result": task.get("result"),
        "error": task.get("error"),
        "error_code": task.get("error_code"),
    }
