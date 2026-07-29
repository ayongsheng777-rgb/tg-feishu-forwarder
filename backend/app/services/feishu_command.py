"""Feishu long-connection command handler (lark-oapi SDK).

Connects to Feishu via WebSocket long-connection mode using the official
``lark-oapi`` SDK, receives message events, parses commands, and triggers
channel fetch operations.

Supported commands (sent in the Feishu group where the bot is a member):

  采集                       — fetch all channels' recent missed messages
  采集 <频道名>               — fetch a specific channel by fuzzy title match
  采集 <日期>                 — fetch all channels for a date (e.g. 采集 2026-07-28)
  采集 <开始> <结束>          — fetch all channels for a date range
  采集 <频道名> <日期>        — fetch specific channel for a date
  采集 <频道名> <开始> <结束> — fetch specific channel for a date range
  帮助 / help                — show available commands

No public URL is required — the SDK establishes an outbound WebSocket.
"""
import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import lark_oapi as lark

from app.core.config import settings
from app.services.feishu_client import feishu_client

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# Date patterns
_DATE_FULL = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
_DATE_SHORT = re.compile(r"^(\d{1,2})[-/](\d{1,2})$")


def _parse_date(token: str):
    """Parse a date token. Returns a naive datetime (midnight) or None."""
    m = _DATE_FULL.match(token)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None

    m = _DATE_SHORT.match(token)
    if m:
        mo, d = int(m[1]), int(m[2])
        y = datetime.now(CST).year
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    return None


def _load_creds_from_db():
    """Load Feishu credentials from the DB settings table (sync)."""
    try:
        db_path = settings.db_url.split("://")[-1]
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM settings WHERE key IN ('feishu_app_id', 'feishu_app_secret')"
        )
        app_id = app_secret = ""
        for key, value in cursor.fetchall():
            if key == "feishu_app_id" and value:
                app_id = value
            elif key == "feishu_app_secret" and value:
                app_secret = value
        conn.close()
        return app_id, app_secret
    except Exception as e:
        logger.debug("Could not load Feishu creds from DB: %s", e)
        return "", ""


class FeishuCommandService:
    """Manages the Feishu WebSocket long-connection and command dispatch."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the Feishu long-connection in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()

        self._thread = threading.Thread(target=self._run, daemon=True, name="feishu-ws")
        self._thread.start()
        logger.info("Feishu command service starting...")

    async def stop(self):
        """Stop the service. The daemon thread will exit on process shutdown."""
        self._running = False
        logger.info("Feishu command service stopped")

    # ------------------------------------------------------------------
    #  Background thread — runs lark.ws.Client.start() (blocking)
    # ------------------------------------------------------------------

    def _run(self):
        """Main loop: load credentials, start SDK client, retry on failure."""
        # The lark-oapi SDK captures the event loop at import time (module-level
        # ``loop = asyncio.get_event_loop()``).  Since the import happens in the
        # main thread where FastAPI's loop is already running, we must override
        # the SDK's module-level ``loop`` with a fresh one for this thread.
        import lark_oapi.ws.client as ws_client_module
        thread_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(thread_loop)
        ws_client_module.loop = thread_loop

        while self._running:
            app_id = settings.feishu_app_id
            app_secret = settings.feishu_app_secret

            if not app_id or not app_secret:
                app_id, app_secret = _load_creds_from_db()

            if not app_id or not app_secret:
                logger.debug("Feishu credentials not configured, retrying in 30s...")
                time.sleep(30)
                continue

            try:
                event_handler = (
                    lark.EventDispatcherHandler.builder("", "")
                    .register_p2_im_message_receive_v1(self._on_message)
                    .build()
                )

                client = lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.INFO,
                )
                logger.info("Feishu long-connection starting (app_id=%s)...", app_id[:10])
                client.start()  # Blocking — runs until disconnected
            except Exception as e:
                logger.error("Feishu long-connection error: %s", e)

            if self._running:
                logger.info("Reconnecting to Feishu in 10s...")
                time.sleep(10)

    # ------------------------------------------------------------------
    #  Event callback (called from lark-oapi thread)
    # ------------------------------------------------------------------

    def _on_message(self, data) -> None:
        """Handle im.message.receive_v1 event.

        Called synchronously from the lark-oapi internal thread.
        Schedules async processing in the main FastAPI event loop.
        """
        try:
            event = getattr(data, "event", None)
            if not event:
                return

            message = getattr(event, "message", None)
            if not message:
                return

            chat_id = getattr(message, "chat_id", "") or ""
            msg_type = getattr(message, "message_type", "") or ""
            content = getattr(message, "content", "{}") or "{}"

            # Only process text messages
            if msg_type != "text":
                return

            # Parse content JSON
            try:
                content_obj = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return

            text = content_obj.get("text", "").strip()
            if not text:
                return

            # Strip @mentions (e.g. "@_user_1")
            text = re.sub(r"@_\w+\s*", "", text).strip()
            if not text:
                return

            logger.info("Feishu command received: chat=%s text=%r", chat_id[:12], text)

            # Schedule async processing in the main event loop
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._process_command(chat_id, text), self._loop
                )

        except Exception:
            logger.exception("Error in Feishu message callback")

    # ------------------------------------------------------------------
    #  Command parsing & execution (async, runs in FastAPI event loop)
    # ------------------------------------------------------------------

    async def _process_command(self, chat_id: str, text: str):
        """Parse and execute a command, then reply with the result."""
        parts = text.split()
        if not parts:
            return

        cmd = parts[0].lower()

        # --- Help ---
        if cmd in ("帮助", "help", "?", "？", "h"):
            await self._send_help(chat_id)
            return

        # --- Fetch ---
        if cmd in ("采集", "fetch", "collect", "cai"):
            args = parts[1:]
            await self._handle_fetch(chat_id, args)
            return

        # Unknown command — ignore silently

    async def _handle_fetch(self, chat_id: str, args: list[str]):
        """Parse fetch arguments and execute the appropriate fetch operation."""
        from app.core.database import async_session
        from app.models import Channel
        from app.services.forwarder import forwarder
        from sqlalchemy import select

        # Parse arguments: separate channel keywords from dates
        channel_parts = []
        dates = []

        for arg in args:
            parsed_date = _parse_date(arg)
            if parsed_date is not None:
                dates.append(parsed_date)
            elif arg in ("所有", "全部", "all"):
                continue  # Explicitly requesting all channels
            else:
                channel_parts.append(arg)

        channel_keyword = " ".join(channel_parts) if channel_parts else None
        start_date = dates[0] if len(dates) >= 1 else None
        end_date = dates[1] if len(dates) >= 2 else (dates[0] if len(dates) == 1 else None)

        # Reply "processing" immediately
        if start_date:
            date_desc = f"{start_date.date()}" if start_date == end_date else f"{start_date.date()}~{end_date.date()}"
            if channel_keyword:
                await feishu_client.send_text(chat_id, f"⏳ 正在采集频道「{channel_keyword}」({date_desc})，请稍候...")
            else:
                await feishu_client.send_text(chat_id, f"⏳ 正在采集所有频道 ({date_desc})，请稍候...")
        else:
            if channel_keyword:
                await feishu_client.send_text(chat_id, f"⏳ 正在采集频道「{channel_keyword}」的未转发消息，请稍候...")
            else:
                await feishu_client.send_text(chat_id, "⏳ 正在采集所有频道的未转发消息，请稍候...")

        try:
            async with async_session() as db:
                # Get matching channels
                result = await db.execute(
                    select(Channel).where(Channel.enabled == True)
                )
                all_channels = result.scalars().all()

                if not all_channels:
                    await feishu_client.send_text(chat_id, "❌ 没有已启用的频道")
                    return

                # Filter by keyword
                if channel_keyword:
                    kw = channel_keyword.lower()
                    matched = [
                        ch for ch in all_channels
                        if kw in (ch.channel_title or "").lower()
                        or kw in (ch.channel_username or "").lower()
                    ]
                    if not matched:
                        titles = [ch.channel_title or ch.channel_id for ch in all_channels]
                        await feishu_client.send_text(
                            chat_id,
                            f"❌ 未找到匹配「{channel_keyword}」的频道\n可用频道: {', '.join(titles)}"
                        )
                        return
                    channels = matched
                else:
                    channels = all_channels

                # Execute fetch for each channel
                results = []
                total_fetched = total_forwarded = total_skipped = total_failed = 0

                for ch in channels:
                    if start_date:
                        r = await forwarder.fetch_by_date(db, ch.id, start_date, end_date)
                    else:
                        r = await forwarder.manual_fetch(db, ch.id, limit=50)

                    if r.get("success"):
                        f, fwd, sk, fl = r["fetched"], r["forwarded"], r["skipped"], r["failed"]
                        total_fetched += f
                        total_forwarded += fwd
                        total_skipped += sk
                        total_failed += fl
                        results.append((ch.channel_title or ch.channel_id, f, fwd, sk, fl))
                    else:
                        msg = r.get("message", "未知错误")
                        results.append((ch.channel_title or ch.channel_id, 0, 0, 0, 0))
                        logger.warning("Fetch failed for %s: %s", ch.channel_title, msg)

                # Build summary
                lines = ["✅ 采集完成\n"]
                if len(results) == 1:
                    name, f, fwd, sk, fl = results[0]
                    lines.append(f"频道: {name}")
                    lines.append(f"拉取: {f} 条")
                    lines.append(f"成功: {fwd} 条")
                    lines.append(f"跳过: {sk} 条")
                    lines.append(f"失败: {fl} 条")
                else:
                    for name, f, fwd, sk, fl in results:
                        lines.append(f"📌 {name}: 拉取{f} 成功{fwd} 跳过{sk} 失败{fl}")
                    lines.append("")
                    lines.append(f"📊 总计: 拉取{total_fetched} 成功{total_forwarded} 跳过{total_skipped} 失败{total_failed}")

                await feishu_client.send_text(chat_id, "\n".join(lines))

        except Exception as e:
            logger.exception("Feishu command: fetch failed")
            await feishu_client.send_text(chat_id, f"❌ 采集失败: {e}")

    async def _send_help(self, chat_id: str):
        """Send the help message."""
        help_text = (
            "📋 飞书采集命令\n\n"
            "1. 采集 — 采集所有频道的未转发消息\n"
            "2. 采集 <频道名> — 采集指定频道\n"
            "3. 采集 <日期> — 按日期采集所有频道\n"
            "   例: 采集 2026-07-28\n"
            "4. 采集 <开始> <结束> — 按日期范围采集\n"
            "   例: 采集 2026-07-25 2026-07-28\n"
            "5. 采集 <频道名> <日期> — 按日期采集指定频道\n"
            "   例: 采集 新闻 2026-07-28\n"
            "6. 采集 <频道名> <开始> <结束> — 按日期范围采集指定频道\n"
            "7. 帮助 — 显示此帮助信息\n\n"
            "💡 频道名支持模糊匹配，日期支持 YYYY-MM-DD 或 MM-DD 格式"
        )
        await feishu_client.send_text(chat_id, help_text)


# Singleton
feishu_command = FeishuCommandService()
