"""Dashboard statistics API — group-level aggregation by feishu chat with channel drill-down."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Channel, ChatAlias, ForwardLog

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Beijing timezone (UTC+8)
_TZ = timezone(timedelta(hours=8))

# Number of channels with no feishu_chat_id — ignore them
_UNBOUND = ""


def _period_starts() -> dict[str, datetime]:
    """Return start datetimes for today / this-week / this-month (Beijing tz)."""
    now = datetime.now(_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = now.weekday()  # Monday=0
    week_start = today_start - timedelta(days=weekday)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "today": today_start,
        "this_week": week_start,
        "this_month": month_start,
    }


async def _count_by_status(
    db: AsyncSession,
    since: datetime | None = None,
    channel_db_id: int | None = None,
    channel_db_ids: list[int] | None = None,
) -> dict:
    """Count forward_logs grouped by status, with optional time / channel filter.

    *channel_db_id*  – single channel (used for per-channel drill-down)
    *channel_db_ids* – multiple channels (used for group-level aggregation)
    """
    conditions = []
    if since is not None:
        conditions.append(ForwardLog.created_at >= since)
    if channel_db_id is not None:
        conditions.append(ForwardLog.channel_id == channel_db_id)
    if channel_db_ids is not None:
        if not channel_db_ids:
            # No channels in this group yet — return zero stats immediately
            return {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        conditions.append(ForwardLog.channel_id.in_(channel_db_ids))

    def _scalar(stmt):
        for c in conditions:
            stmt = stmt.where(c)
        return db.scalar(stmt)

    total = _scalar(select(func.count(ForwardLog.id)))
    success = _scalar(select(func.count(ForwardLog.id)).where(ForwardLog.status == "success"))
    failed = _scalar(select(func.count(ForwardLog.id)).where(ForwardLog.status == "failed"))
    skipped = _scalar(select(func.count(ForwardLog.id)).where(ForwardLog.status == "skipped"))

    t, s, f, sk = await total, await success, await failed, await skipped
    return {
        "total": t or 0,
        "success": s or 0,
        "failed": f or 0,
        "skipped": sk or 0,
    }


async def _resolve_group_name(db: AsyncSession, feishu_chat_id: str) -> str:
    """Resolve a display name for a feishu_chat_id via ChatAlias, fallback to the raw ID."""
    alias = await db.scalar(
        select(ChatAlias.name).where(ChatAlias.feishu_chat_id == feishu_chat_id)
    )
    return alias or feishu_chat_id


@router.get("/dashboard")
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Dashboard statistics grouped by feishu group (chat).

    Returns:
      - overall / by_period: global aggregates
      - groups: list of group-level aggregates, each containing:
          - group_name, feishu_chat_id, channel_count
          - today / this_week / this_month / all_time stats
          - channels: per-channel drill-down within the group

    Automatically reflects channel / alias additions and deletions — live DB queries.
    """
    periods = _period_starts()

    # --- Global aggregates ---
    overall = await _count_by_status(db)
    by_period: dict[str, dict] = {}
    for name, start in periods.items():
        by_period[name] = await _count_by_status(db, since=start)

    # --- Load all channels + alias map ---
    result = await db.execute(select(Channel).order_by(Channel.id))
    channels = result.scalars().all()

    alias_result = await db.execute(select(ChatAlias))
    all_aliases = alias_result.scalars().all()
    alias_map: dict[str, str] = {a.feishu_chat_id: a.name for a in all_aliases}
    # Also collect alias → alias_id for reference
    alias_id_map: dict[str, int] = {a.feishu_chat_id: a.id for a in all_aliases}

    # --- Group channels by feishu_chat_id ---
    groups_map: dict[str, list[Channel]] = {}
    for ch in channels:
        fid = ch.feishu_chat_id
        if fid == _UNBOUND:
            continue  # skip channels with no target feishu group
        groups_map.setdefault(fid, []).append(ch)

    # --- Also seed groups from aliases that have no channels yet ---
    # This ensures newly created groups appear immediately (with 0 stats),
    # even before any channel is bound to them.
    for alias in all_aliases:
        fid = alias.feishu_chat_id
        if fid not in groups_map:
            groups_map[fid] = []  # empty channel list

    # --- Build group-level aggregates ---
    groups: list[dict] = []
    for fid, ch_list in groups_map.items():
        group_name = alias_map.get(fid, fid)
        ch_ids = [ch.id for ch in ch_list]

        entry: dict = {
            "feishu_chat_id": fid,
            "group_name": group_name,
            "channel_count": len(ch_list),
        }
        # Group-level stats (aggregate all channels under this group)
        for period_name, start in periods.items():
            entry[period_name] = await _count_by_status(db, since=start, channel_db_ids=ch_ids)
        entry["all_time"] = await _count_by_status(db, channel_db_ids=ch_ids)

        # Per-channel drill-down
        entry["channels"] = []
        for ch_obj in ch_list:
            ch_entry: dict = {
                "id": ch_obj.id,
                "tg_channel_id": ch_obj.channel_id,
                "channel_title": ch_obj.channel_title or ch_obj.channel_username or ch_obj.channel_id,
                "enabled": ch_obj.enabled,
            }
            for period_name, start in periods.items():
                ch_entry[period_name] = await _count_by_status(
                    db, since=start, channel_db_id=ch_obj.id
                )
            ch_entry["all_time"] = await _count_by_status(db, channel_db_id=ch_obj.id)
            entry["channels"].append(ch_entry)

        # Sort channels within group: most all_time total first
        entry["channels"].sort(key=lambda c: c["all_time"]["total"], reverse=True)
        groups.append(entry)

    # Sort groups: most all_time total first
    groups.sort(key=lambda g: g["all_time"]["total"], reverse=True)

    return {
        "overall": overall,
        "by_period": by_period,
        "groups": groups,
    }
