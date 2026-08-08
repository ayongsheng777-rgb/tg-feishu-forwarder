"""Periodic catch-up scheduler.

Two jobs, both on a single background loop:

1. **Catch-up sweep** — every ``catchup_interval_minutes`` (default 2) it runs
   ``forwarder.manual_fetch`` for all enabled channels.  This guarantees that
   messages are forwarded even when the realtime listener misses them
   (e.g. the TG account isn't a subscriber of a *public* channel, or the
   listener was down for a while).  It directly fixes the "followed + configured
   but collected zero" class of bug without depending on realtime delivery.

2. **TG health watchdog** — every loop tick it checks the TG connection and
   notifies the configured ``report_chat_id`` group on disconnect / recovery,
   so the operator knows when re-login (``tg登录``) is needed.
"""
import asyncio
import logging
import time
from datetime import timedelta, timezone

from sqlalchemy import select

from app.models import Channel, Setting

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# How often to check TG health / attempt catch-up ticks (seconds)
_TICK = 30


async def _get_setting(db, key: str, default: str = "") -> str:
    result = await db.execute(select(Setting).where(Setting.key == key))
    s = result.scalar_one_or_none()
    return (s.value if s and s.value is not None else default) or default


class CatchupScheduler:
    """Background loop driving periodic catch-up + TG health checks."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._last_catchup: float = 0.0
        self._tg_down: bool = False

    def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Catchup scheduler started")

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
        from app.services.feishu_client import feishu_client
        from app.services.telegram_client import tg_manager

        while True:
            try:
                await asyncio.sleep(_TICK)

                async with async_session() as db:
                    interval_str = await _get_setting(db, "catchup_interval_minutes", "2")
                    report_chat = await _get_setting(db, "report_chat_id", "")
                try:
                    interval = int(interval_str)
                except ValueError:
                    interval = 2

                # --- TG health watchdog ---
                await self._check_tg(db=None, report_chat=report_chat,
                                     feishu_client=feishu_client, tg_manager=tg_manager)

                # --- Periodic catch-up sweep ---
                if interval > 0 and time.time() - self._last_catchup >= interval * 60:
                    await self._catchup()
                    self._last_catchup = time.time()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Catchup scheduler error")

    async def _check_tg(self, db, report_chat: str, feishu_client, tg_manager) -> None:
        connected = tg_manager.is_connected
        authorized = False
        if connected:
            try:
                st = await tg_manager.get_login_status()
                authorized = st.get("authorized", False)
            except Exception:
                authorized = False
        ok = connected and authorized

        if not ok and not self._tg_down:
            self._tg_down = True
            if report_chat:
                try:
                    await feishu_client.send_text(
                        report_chat,
                        "⚠️ Telegram 连接已断开或登录失效，实时监听暂停。\n"
                        "如需重新登录，请在飞书发送：tg登录 <手机号>",
                    )
                except Exception:
                    logger.warning("notify tg-down failed", exc_info=True)
        elif ok and self._tg_down:
            self._tg_down = False
            if report_chat:
                try:
                    await feishu_client.send_text(report_chat, "✅ Telegram 连接已恢复，实时监听继续。")
                except Exception:
                    logger.warning("notify tg-up failed", exc_info=True)
            # Reload the listener so it's definitely running on the restored session
            try:
                from app.api.telegram import reload_listener_impl
                from app.core.database import async_session

                async with async_session() as sdb:
                    await reload_listener_impl(sdb)
            except Exception:
                logger.warning("reload after tg recovery failed", exc_info=True)

    async def _catchup(self) -> None:
        from app.core.database import async_session
        from app.services.forwarder import forwarder

        async with async_session() as db:
            result = await db.execute(select(Channel).where(Channel.enabled == True))
            channels = result.scalars().all()

            forwarded = 0
            for ch in channels:
                try:
                    r = await forwarder.manual_fetch(db, ch.id, limit=50)
                    if r.get("success"):
                        forwarded += r.get("forwarded", 0)
                except Exception:
                    logger.exception("Catchup failed for channel %s", ch.channel_title)
            if forwarded:
                logger.info("Catchup sweep forwarded %d messages", forwarded)


catchup_scheduler = CatchupScheduler()
