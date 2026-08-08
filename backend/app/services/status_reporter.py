"""Runtime status reporter.

Builds a human-readable status report (listener state, TG connection,
24h forwarding stats, recent errors) and can push it to a designated
Feishu group — either on demand (``tg状态`` command / API) or periodically
via a background task configured with the ``report_chat_id`` and
``report_interval_minutes`` settings.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, desc

from app.models import Channel, ForwardLog, Setting

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

_STARTED_AT = time.time()


async def _get_setting(db, key: str, default: str = "") -> str:
    result = await db.execute(select(Setting).where(Setting.key == key))
    s = result.scalar_one_or_none()
    return (s.value if s and s.value is not None else default) or default


def _fmt_uptime() -> str:
    secs = int(time.time() - _STARTED_AT)
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    minutes, _ = divmod(secs, 60)
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分钟"


async def build_report(db) -> str:
    """Assemble the status report text."""
    from app.services.telegram_client import tg_manager

    now = datetime.now(CST)

    # --- listener & connection state ---
    task = tg_manager._listener_task
    listener_running = task is not None and not task.done()
    try:
        tg_status = await tg_manager.get_login_status()
        tg_ok = tg_status.get("authorized", False)
        tg_user = (tg_status.get("user") or {}).get("first_name") or ""
    except Exception:
        tg_ok, tg_user = False, ""

    # 补抓调度是否开启（作为实时监听的兜底）
    catchup_on = False
    try:
        ci = await _get_setting(db, "catchup_interval_minutes", "2")
        catchup_on = (int(ci) if ci else 0) > 0
    except (ValueError, Exception):
        catchup_on = False

    if listener_running:
        listener_state = "✅ 实时运行中"
    elif catchup_on:
        listener_state = "⚠️ 实时已停（补抓保底投递中）"
    else:
        listener_state = "⏸ 已停止"

    # --- channels ---
    total_ch = await db.scalar(select(func.count(Channel.id))) or 0
    enabled_ch = await db.scalar(
        select(func.count(Channel.id)).where(Channel.enabled == True)
    ) or 0

    # --- stats: total + last 24h ---
    total_ok = await db.scalar(
        select(func.count(ForwardLog.id)).where(ForwardLog.status == "success")
    ) or 0
    total_fail = await db.scalar(
        select(func.count(ForwardLog.id)).where(ForwardLog.status == "failed")
    ) or 0
    since = now.replace(tzinfo=None) - timedelta(hours=24)
    day_ok = await db.scalar(
        select(func.count(ForwardLog.id)).where(
            ForwardLog.status == "success", ForwardLog.created_at >= since
        )
    ) or 0
    day_fail = await db.scalar(
        select(func.count(ForwardLog.id)).where(
            ForwardLog.status == "failed", ForwardLog.created_at >= since
        )
    ) or 0
    day_skip = await db.scalar(
        select(func.count(ForwardLog.id)).where(
            ForwardLog.status == "skipped", ForwardLog.created_at >= since
        )
    ) or 0

    # --- recent errors (up to 3) ---
    result = await db.execute(
        select(ForwardLog)
        .where(ForwardLog.status == "failed")
        .order_by(desc(ForwardLog.id))
        .limit(3)
    )
    recent_errors = result.scalars().all()

    overall = (
        "🟢 正常"
        if (listener_running and tg_ok)
        else ("🟠 实时监听降级" if (catchup_on and tg_ok) else ("🟠 异常" if tg_ok else "🔴 阻断"))
    )

    lines = [
        f"📊 青鸟 · 运行状态报告",
        f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"总体状态: {overall}",
        f"运行时长: {_fmt_uptime()}",
        f"TG 连接: {'✅ 已连接' + (f'（{tg_user}）' if tg_user else '') if tg_ok else '❌ 未连接'}",
        f"消息监听: {listener_state}",
        f"监控频道: {enabled_ch} 启用 / {total_ch} 总计",
        "",
        f"📈 近24小时: 成功 {day_ok} | 失败 {day_fail} | 跳过 {day_skip}",
        f"📚 历史累计: 成功 {total_ok} | 失败 {total_fail}",
    ]

    if recent_errors:
        lines.append("")
        lines.append("⚠️ 最近错误:")
        for log in recent_errors:
            err = (log.error_message or "").strip().split("\n")[0][:60]
            ts = log.created_at.strftime("%m-%d %H:%M") if log.created_at else ""
            lines.append(f"  · [{ts}] {err}")

    return "\n".join(lines)


async def send_report(chat_id: str | None = None) -> dict:
    """Build and send the report. Defaults to the configured report_chat_id."""
    from app.core.database import async_session
    from app.services.feishu_client import feishu_client

    async with async_session() as db:
        if not chat_id:
            chat_id = await _get_setting(db, "report_chat_id", "")
        if not chat_id:
            return {"success": False, "message": "未配置报告目标群（report_chat_id）"}
        text = await build_report(db)

    resp = await feishu_client.send_text(chat_id, text)
    ok = resp.get("code") == 0
    return {"success": ok, "message": "已发送" if ok else str(resp), "chat_id": chat_id}


class StatusReportScheduler:
    """Periodic report push — re-reads settings every minute."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._last_sent: float = 0.0

    def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Status report scheduler started")

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self):
        from app.core.database import async_session

        while True:
            try:
                await asyncio.sleep(60)
                async with async_session() as db:
                    chat_id = await _get_setting(db, "report_chat_id", "")
                    interval_str = await _get_setting(db, "report_interval_minutes", "0")
                try:
                    interval = int(interval_str)
                except ValueError:
                    interval = 0
                if not chat_id or interval <= 0:
                    continue
                if time.time() - self._last_sent < interval * 60:
                    continue
                result = await send_report(chat_id)
                if result.get("success"):
                    self._last_sent = time.time()
                    logger.info("Periodic status report sent to %s", chat_id[:12])
                else:
                    logger.warning("Periodic status report failed: %s", result.get("message"))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Status report scheduler error")


report_scheduler = StatusReportScheduler()
