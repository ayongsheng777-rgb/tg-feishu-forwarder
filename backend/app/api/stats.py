"""Dashboard statistics API — group-level aggregation by feishu chat with channel drill-down."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Channel, ChatAlias, ForwardLog

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Beijing timezone (UTC+8)
_TZ = timezone(timedelta(hours=8))

# Number of channels with no feishu_chat_id — ignore them
_UNBOUND = ""

# Statuses that get their own bucket (anything else only counts toward ``total``,
# matching the previous behaviour).
_STATUSES = ("success", "failed", "skipped")

# Periods reported per channel / group / globally.
_PERIODS = ("today", "this_week", "this_month", "all_time")


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


def _empty_stats() -> dict:
    return {"total": 0, "success": 0, "failed": 0, "skipped": 0}


def _add_stats(target: dict, source: dict) -> dict:
    """Accumulate ``source`` counts into ``target`` (in place)."""
    for key in ("total", "success", "failed", "skipped"):
        target[key] += source.get(key, 0)
    return target


def _sum_stats(chunks) -> dict:
    """Sum an iterable of ``{total,success,failed,skipped}`` dicts."""
    out = _empty_stats()
    for chunk in chunks:
        _add_stats(out, chunk)
    return out


async def _count_by_status(
    db: AsyncSession,
    since: datetime | None = None,
    channel_db_id: int | None = None,
    channel_db_ids: list[int] | None = None,
) -> dict:
    """Count forward_logs grouped by status, with optional time / channel filter.

    Kept for ad-hoc callers (e.g. the ``tg统计`` Feishu command). The dashboard
    no longer uses it — it does a single grouped pass instead, see
    :func:`_aggregate_by_channel`.

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
            return _empty_stats()
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


async def _aggregate_by_channel(db: AsyncSession, periods: dict[str, datetime]) -> dict:
    """Single grouped pass over ``forward_logs``.

    Returns ``{channel_id: {period: {total, success, failed, skipped}}}`` where
    ``channel_id`` may be ``None`` for logs whose channel has been deleted.

    Why: the old implementation issued four COUNTs per (group × period) and per
    (channel × period) — with 6 groups / 33 channels that is ~2,560 queries, each
    a full table scan, which made ``/api/stats/dashboard`` take ~30s. This is
    one ``GROUP BY channel_id, status`` with conditional sums instead.
    """
    today, week, month = periods["today"], periods["this_week"], periods["this_month"]

    rows = await db.execute(
        select(
            ForwardLog.channel_id,
            ForwardLog.status,
            func.count(ForwardLog.id),
            func.sum(case((ForwardLog.created_at >= today, 1), else_=0)),
            func.sum(case((ForwardLog.created_at >= week, 1), else_=0)),
            func.sum(case((ForwardLog.created_at >= month, 1), else_=0)),
        ).group_by(ForwardLog.channel_id, ForwardLog.status)
    )

    result: dict = {}
    for channel_id, status, n_all, n_today, n_week, n_month in rows.all():
        bucket = result.setdefault(
            channel_id, {period: _empty_stats() for period in _PERIODS}
        )
        for period, count in (
            ("all_time", n_all),
            ("today", n_today),
            ("this_week", n_week),
            ("this_month", n_month),
        ):
            count = count or 0
            bucket[period]["total"] += count
            if status in _STATUSES:
                bucket[period][status] += count
    return result


def _zero_bucket() -> dict:
    return {period: _empty_stats() for period in _PERIODS}


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

    # One pass over forward_logs for every channel/status combination.
    per_channel = await _aggregate_by_channel(db, periods)

    # --- Global aggregates (all logs, including channels with no binding) ---
    overall = _sum_stats(b["all_time"] for b in per_channel.values())
    by_period = {
        name: _sum_stats(b[name] for b in per_channel.values()) for name in periods
    }

    # --- Load all channels + alias map ---
    result = await db.execute(select(Channel).order_by(Channel.id))
    channels = result.scalars().all()

    alias_result = await db.execute(select(ChatAlias))
    all_aliases = alias_result.scalars().all()
    alias_map: dict[str, str] = {a.feishu_chat_id: a.name for a in all_aliases}

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
        entry: dict = {
            "feishu_chat_id": fid,
            "group_name": alias_map.get(fid, fid),
            "channel_count": len(ch_list),
        }
        # Group-level stats (aggregate all channels under this group)
        for period_name in periods:
            entry[period_name] = _sum_stats(
                per_channel.get(ch.id, _zero_bucket())[period_name] for ch in ch_list
            )
        entry["all_time"] = _sum_stats(
            per_channel.get(ch.id, _zero_bucket())["all_time"] for ch in ch_list
        )

        # Per-channel drill-down
        entry["channels"] = []
        for ch_obj in ch_list:
            stats = per_channel.get(ch_obj.id) or _zero_bucket()
            ch_entry: dict = {
                "id": ch_obj.id,
                "tg_channel_id": ch_obj.channel_id,
                "channel_title": ch_obj.channel_title or ch_obj.channel_username or ch_obj.channel_id,
                "enabled": ch_obj.enabled,
            }
            for period_name in periods:
                ch_entry[period_name] = stats[period_name]
            ch_entry["all_time"] = stats["all_time"]
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
