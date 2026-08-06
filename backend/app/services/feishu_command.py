"""Feishu long-connection command handler (lark-oapi SDK).

Connects to Feishu via WebSocket long-connection mode using the official
``lark-oapi`` SDK, receives message events, parses commands, and triggers
channel fetch operations.

All commands use a ``tg`` prefix to avoid conflicts with other projects
sharing the same Feishu bot.

Supported commands (sent in the Feishu group where the bot is a member):

  tg采集                     — fetch all channels' recent missed messages
  tg采集 <频道名>             — fetch a specific channel by fuzzy title match
  tg采集 <群名称>             — resolve a bound group/alias name and fetch the
                               channels bound to that Feishu group
  tg采集 <日期>               — fetch all channels for a date (e.g. tg采集 2026-07-28)
  tg采集 <开始> <结束>        — fetch all channels for a date range
  tg采集 <名称> <日期>        — fetch specific channel/group for a date
  tg采集 <名称> <开始> <结束> — fetch specific channel/group for a date range
  tg绑定 <名称> <群ID>        — bind a group name to a Feishu chat ID
  tg群列表                    — list known group name <-> chat ID bindings
  tg状态 [群名称]             — send a runtime status report (to this chat,
                               or to the named group if given)
  tg帮助                      — show available commands

Group-name recognition: incoming groups are auto-discovered via the Feishu
API (group name <-> chat_id), and manual bindings can be added in the admin
UI or with ``tg绑定``.

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
                # Auto-discover the group's name -> chat_id binding in the
                # background so future commands can reference it by name.
                asyncio.run_coroutine_threadsafe(
                    self._auto_discover_chat(chat_id), self._loop
                )

        except Exception:
            logger.exception("Error in Feishu message callback")

    # ------------------------------------------------------------------
    #  Command parsing & execution (async, runs in FastAPI event loop)
    # ------------------------------------------------------------------

    async def _process_command(self, chat_id: str, text: str):
        """Parse and execute a command, then reply with the result.

        All commands are prefixed with ``tg`` to avoid conflicts with other
        projects sharing the same Feishu bot.  Both ``tg采集`` and
        ``tg 采集`` are accepted.
        """
        # Normalise: "tg 帮助" → "tg帮助", "tg 采集 新闻" → "tg采集 新闻"
        text = text.strip()
        if text.lower().startswith("tg "):
            text = "tg" + text[3:]

        parts = text.split()
        if not parts:
            return

        cmd = parts[0].lower()

        # --- Help ---
        if cmd in ("tg帮助", "tghelp", "tg?"):
            await self._send_help(chat_id)
            return

        # --- Fetch ---
        if cmd in ("tg采集", "tgfetch", "tgcollect"):
            args = parts[1:]
            await self._handle_fetch(chat_id, args)
            return

        # --- Bind group name -> chat ID ---
        if cmd in ("tg绑定", "tgbind"):
            await self._handle_bind(chat_id, parts[1:])
            return

        # --- List known group bindings ---
        if cmd in ("tg群列表", "tg群绑定", "tgchats"):
            await self._handle_list_chats(chat_id)
            return

        # --- Status report ---
        if cmd in ("tg状态", "tg报告", "tgstatus", "tgreport"):
            await self._handle_status(chat_id, parts[1:])
            return

        # Unknown command — ignore silently

    async def _auto_discover_chat(self, chat_id: str):
        """Register/refresh the group's name binding (background)."""
        try:
            from app.core.database import async_session
            from app.services import alias_resolver

            async with async_session() as db:
                alias = await alias_resolver.auto_discover(db, chat_id)
                if alias:
                    logger.info(
                        "Chat alias auto-discovered: %s -> %s",
                        alias.name, chat_id[:12],
                    )
        except Exception:
            logger.debug("auto_discover failed for %s", chat_id[:12], exc_info=True)

    async def _handle_bind(self, chat_id: str, args: list[str]):
        """tg绑定 <名称> <chat_id> — manual name -> chat ID binding."""
        from app.core.database import async_session
        from app.services import alias_resolver

        if len(args) < 2:
            await feishu_client.send_text(
                chat_id,
                "❌ 用法: tg绑定 <名称> <飞书群ID>\n例: tg绑定 科技 oc_a8797b4b11f1398ca1cc818a645efbe1",
            )
            return

        name, target_chat = args[0].strip(), args[1].strip()
        if not target_chat.startswith("oc_"):
            await feishu_client.send_text(
                chat_id, f"❌ 飞书群ID应以 oc_ 开头，收到: {target_chat}"
            )
            return

        try:
            async with async_session() as db:
                alias = await alias_resolver.bind_alias(db, name, target_chat, auto=False)
            await feishu_client.send_text(
                chat_id, f"✅ 已绑定: 「{alias.name}」 → {alias.feishu_chat_id}"
            )
        except Exception as e:
            logger.exception("Feishu command: bind failed")
            await feishu_client.send_text(chat_id, f"❌ 绑定失败: {e}")

    async def _handle_list_chats(self, chat_id: str):
        """tg群列表 — show all known name -> chat ID bindings."""
        from app.core.database import async_session
        from app.services import alias_resolver

        try:
            async with async_session() as db:
                aliases = await alias_resolver.list_aliases(db, only_enabled=False)
            if not aliases:
                await feishu_client.send_text(
                    chat_id,
                    "📭 暂无群绑定\n"
                    "机器人所在群会自动识别登记；也可用 tg绑定 <名称> <群ID> 手动绑定。",
                )
                return
            lines = ["📋 群名称绑定列表\n"]
            for a in aliases:
                tag = "自动" if a.auto_discovered else "手动"
                state = "" if a.enabled else "（已停用）"
                lines.append(f"· {a.name}{state} [{tag}]\n  {a.feishu_chat_id}")
            await feishu_client.send_text(chat_id, "\n".join(lines))
        except Exception as e:
            logger.exception("Feishu command: list chats failed")
            await feishu_client.send_text(chat_id, f"❌ 查询失败: {e}")

    async def _handle_status(self, chat_id: str, args: list[str]):
        """tg状态 [群名称] — send the runtime status report."""
        from app.core.database import async_session
        from app.services import alias_resolver, status_reporter

        target = chat_id
        if args:
            # Resolve a named group; fall back to the configured report group
            async with async_session() as db:
                alias = await alias_resolver.resolve_name(db, " ".join(args))
            if alias:
                target = alias.feishu_chat_id
            else:
                await feishu_client.send_text(
                    chat_id,
                    f"❌ 未找到群名称「{' '.join(args)}」的绑定，可用 tg群列表 查看",
                )
                return

        await feishu_client.send_text(chat_id, "⏳ 正在生成状态报告...")
        try:
            result = await status_reporter.send_report(target)
            if not result.get("success"):
                await feishu_client.send_text(
                    chat_id, f"❌ 报告发送失败: {result.get('message')}"
                )
        except Exception as e:
            logger.exception("Feishu command: status report failed")
            await feishu_client.send_text(chat_id, f"❌ 报告发送失败: {e}")

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

                # Filter by keyword: TG channel title first, then group alias
                alias_used = None
                if channel_keyword:
                    kw = channel_keyword.lower()
                    matched = [
                        ch for ch in all_channels
                        if kw in (ch.channel_title or "").lower()
                        or kw in (ch.channel_username or "").lower()
                    ]
                    if not matched:
                        # Fall back to group-name recognition: resolve the
                        # keyword as a bound Feishu group/alias name and
                        # collect the channels whose target group it is.
                        from app.services import alias_resolver
                        alias_used = await alias_resolver.resolve_name(db, channel_keyword)
                        if alias_used:
                            matched = [
                                ch for ch in all_channels
                                if ch.feishu_chat_id == alias_used.feishu_chat_id
                            ]
                    if not matched:
                        titles = [ch.channel_title or ch.channel_id for ch in all_channels]
                        hint = ""
                        if alias_used:
                            hint = f"\n（已识别群「{alias_used.name}」，但该群未绑定任何启用频道）"
                        await feishu_client.send_text(
                            chat_id,
                            f"❌ 未找到匹配「{channel_keyword}」的频道或群绑定{hint}\n"
                            f"可用频道: {', '.join(titles)}\n"
                            f"💡 可用 tg绑定 {channel_keyword} <飞书群ID> 建立群绑定"
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
                if alias_used:
                    lines.append(f"🏷 按群绑定「{alias_used.name}」路由采集")
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
            "📋 TG转发采集命令\n\n"
            "1. tg采集 — 采集所有频道的未转发消息\n"
            "2. tg采集 <频道名> — 采集指定频道\n"
            "3. tg采集 <群名称> — 按群绑定采集（如: tg采集 科技）\n"
            "4. tg采集 <日期> — 按日期采集所有频道\n"
            "   例: tg采集 2026-07-28\n"
            "5. tg采集 <开始> <结束> — 按日期范围采集\n"
            "   例: tg采集 2026-07-25 2026-07-28\n"
            "6. tg采集 <名称> <日期> — 按日期采集指定频道/群\n"
            "   例: tg采集 新闻 2026-07-28\n"
            "7. tg采集 <名称> <开始> <结束> — 按日期范围采集指定频道/群\n"
            "8. tg绑定 <名称> <群ID> — 绑定群名称到飞书群\n"
            "   例: tg绑定 科技 oc_a8797b4b11f1398ca1cc818a645efbe1\n"
            "9. tg群列表 — 查看所有群名称绑定\n"
            "10. tg状态 [群名称] — 发送运行状态报告（默认发到当前群）\n"
            "11. tg帮助 — 显示此帮助信息\n\n"
            "💡 频道名/群名称支持模糊匹配；机器人所在群会自动识别登记；\n"
            "日期支持 YYYY-MM-DD 或 MM-DD 格式"
        )
        await feishu_client.send_text(chat_id, help_text)


# Singleton
feishu_command = FeishuCommandService()
