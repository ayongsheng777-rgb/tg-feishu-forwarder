"""Forward logs API — list with pagination + stats + retry + clear."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, desc, delete
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session
from app.models import ForwardLog
from app.schemas.forward_log import ForwardLogOut
from app.services.forwarder import forwarder

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=list[ForwardLogOut])
async def list_logs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status: str = Query("", description="Filter by status"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ForwardLog).order_by(desc(ForwardLog.id))
    if status:
        stmt = stmt.where(ForwardLog.status == status)
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/stats")
async def log_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate forwarding statistics."""
    total = await db.scalar(select(func.count(ForwardLog.id)))
    success = await db.scalar(select(func.count(ForwardLog.id)).where(ForwardLog.status == "success"))
    failed = await db.scalar(select(func.count(ForwardLog.id)).where(ForwardLog.status == "failed"))
    skipped = await db.scalar(select(func.count(ForwardLog.id)).where(ForwardLog.status == "skipped"))
    return {
        "total": total or 0,
        "success": success or 0,
        "failed": failed or 0,
        "skipped": skipped or 0,
    }


@router.post("/{log_id}/retry")
async def retry_log(log_id: int, db: AsyncSession = Depends(get_db)):
    """Retry a single failed forward."""
    async with async_session() as session:
        result = await forwarder.retry_forward(session, log_id)
    return result


@router.post("/retry-failed")
async def retry_all_failed(db: AsyncSession = Depends(get_db)):
    """Retry all failed forwards."""
    stmt = select(ForwardLog).where(ForwardLog.status == "failed").order_by(ForwardLog.id)
    result = await db.execute(stmt)
    failed_logs = result.scalars().all()

    results = []
    for log in failed_logs:
        async with async_session() as session:
            r = await forwarder.retry_forward(session, log.id)
            results.append({"log_id": log.id, "tg_message_id": log.tg_message_id, **r})

    succeeded = sum(1 for r in results if r.get("success"))
    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "details": results,
    }


@router.delete("")
async def clear_logs(
    status: str = Query("", description="只清理指定状态: success/failed/skipped/pending，空=全部"),
    before: str = Query("", description="只清理该时间之前的记录 (ISO 8601, 如 2026-01-01T00:00:00)，空=不限"),
    db: AsyncSession = Depends(get_db),
):
    """Clear forwarding logs. Optional status / before filters; returns deleted count."""
    conditions = []
    if status:
        if status not in ("success", "failed", "skipped", "pending"):
            return JSONResponse(
                status_code=400,
                content={"detail": "status 仅支持 success/failed/skipped/pending"},
            )
        conditions.append(ForwardLog.status == status)
    if before:
        try:
            dt = datetime.fromisoformat(before)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "before 格式错误，需 ISO 8601 (如 2026-01-01T00:00:00)"},
            )
        conditions.append(ForwardLog.created_at < dt)

    stmt = delete(ForwardLog)
    for c in conditions:
        stmt = stmt.where(c)
    result = await db.execute(stmt)
    await db.commit()
    return {"deleted": result.rowcount}
