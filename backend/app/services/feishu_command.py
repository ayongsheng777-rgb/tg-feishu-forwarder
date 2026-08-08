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
        # Pending TG re-login state (driven via Feishu commands)
        self._pending_login_phone: str = ""
        self._pending_login_code: str = ""

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
        # Normalise: case-insensitive TG prefix, with or without a space.
        # "TG帮助" / "tg 帮助" / "Tg帮助" / "tG帮助" all become "tg帮助".
        text = text.strip()
        m = re.match(r"^tg\s*", text, re.IGNORECASE)
        if m:
            text = "tg" + text[m.end():]

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

        # --- Pause / resume channel ---
        if cmd in ("tg暂停", "tgpause"):
            await self._handle_pause(chat_id, parts[1:], pause=True)
            return
        if cmd in ("tg恢复", "tgresume", "tg继续"):
            await self._handle_pause(chat_id, parts[1:], pause=False)
            return

        # --- Remove channel ---
        if cmd in ("tg移除", "tg删除", "tgremove", "tgdelete"):
            await self._handle_remove(chat_id, parts[1:])
            return

        # --- Listener on/off ---
        if cmd in ("tg监听", "tglisten", "tg监控", "tgmonitor"):
            await self._handle_listener(chat_id, parts[1:])
            return

        # --- Restart system ---
        if cmd in ("tg重启", "tg重启系统", "tgrestart"):
            await self._handle_restart(chat_id)
            return

        # --- TG re-login flow (driven from Feishu) ---
        if cmd in ("tg登录", "tglogin"):
            await self._handle_login(chat_id, parts[1:])
            return
        if cmd in ("tg验证码", "tgcode"):
            await self._handle_code(chat_id, parts[1:])
            return
        if cmd in ("tg密码", "tgpwd"):
            await self._handle_password(chat_id, parts[1:])
            return

        # --- Catch-up interval setting ---
        if cmd in ("tg补抓", "tgcatchup"):
            await self._handle_catchup_interval(chat_id, parts[1:])
            return

        # --- Subscribe to a TG channel + bind forward group (root fix) ---
        if cmd in ("tg订阅", "tgsub", "tg关注", "tgjoin"):
            await self._handle_subscribe(chat_id, parts[1:])
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
            # Verify bot membership in the target group
            try:
                real_name = await feishu_client.get_chat_name(target_chat)
                extra = f"\n群实际名称: {real_name}" if real_name else ""
            except Exception:
                extra = "\n⚠️ 注意：机器人不在该群内，转发会失败——请先把机器人拉进该群"
            await feishu_client.send_text(
                chat_id, f"✅ 已绑定: 「{alias.name}」 → {alias.feishu_chat_id}{extra}"
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

    # ------------------------------------------------------------------
    #  Channel management: pause / resume / remove
    # ------------------------------------------------------------------

    async def _resolve_channel(self, db, name: str):
        """Fuzzy-match a channel by title / username / id (case-insensitive)."""
        from sqlalchemy import select
        from app.models import Channel

        kw = name.lower().strip()
        result = await db.execute(select(Channel))
        channels = result.scalars().all()
        for ch in channels:
            if kw == str(ch.channel_id):
                return ch
        for ch in channels:
            if kw in (ch.channel_title or "").lower() or kw in (ch.channel_username or "").lower():
                return ch
        return None

    async def _handle_pause(self, chat_id: str, args: list[str], pause: bool):
        """tg暂停 <频道名> / tg恢复 <频道名> — toggle channel.enabled + reload."""
        if not args:
            await feishu_client.send_text(chat_id, "❌ 用法: tg暂停 <频道名> / tg恢复 <频道名>")
            return
        name = " ".join(args).strip()
        from app.core.database import async_session
        from app.api.telegram import reload_listener_impl

        try:
            async with async_session() as db:
                ch = await self._resolve_channel(db, name)
                if not ch:
                    await feishu_client.send_text(chat_id, f"❌ 未找到频道「{name}」")
                    return
                title = ch.channel_title or ch.channel_username or ch.channel_id
                ch.enabled = not pause
                await db.commit()
                await reload_listener_impl(db)
            state = "已暂停（不再转发）" if pause else "已恢复（实时转发恢复）"
            await feishu_client.send_text(chat_id, f"✅ {title}：{state}")
        except Exception as e:
            logger.exception("pause/resume failed")
            await feishu_client.send_text(chat_id, f"❌ 操作失败: {e}")

    async def _handle_remove(self, chat_id: str, args: list[str]):
        """tg移除 <频道名> — delete a channel from the DB + reload listener."""
        if not args:
            await feishu_client.send_text(chat_id, "❌ 用法: tg移除 <频道名>")
            return
        name = " ".join(args).strip()
        from app.core.database import async_session
        from app.api.telegram import reload_listener_impl

        try:
            async with async_session() as db:
                ch = await self._resolve_channel(db, name)
                if not ch:
                    await feishu_client.send_text(chat_id, f"❌ 未找到频道「{name}」")
                    return
                title = ch.channel_title or ch.channel_username or ch.channel_id
                await db.delete(ch)
                await db.commit()
                await reload_listener_impl(db)
            await feishu_client.send_text(chat_id, f"🗑 已移除频道: {title}")
        except Exception as e:
            logger.exception("remove channel failed")
            await feishu_client.send_text(chat_id, f"❌ 移除失败: {e}")

    # ------------------------------------------------------------------
    #  Listener on/off + system restart
    # ------------------------------------------------------------------

    async def _handle_listener(self, chat_id: str, args: list[str]):
        """tg监听 开启 / tg监听 关闭 — start or stop the realtime listener."""
        from app.core.database import async_session
        from app.api.telegram import start_listener_impl, _set_listener_pref
        from app.services.telegram_client import tg_manager

        arg = (args[0].lower() if args else "")
        on = arg in ("开", "开启", "on", "start", "1", "true", "yes")
        off = arg in ("关", "关闭", "off", "stop", "0", "false", "no")

        if not arg or (not on and not off):
            running = tg_manager._listener_task is not None and not tg_manager._listener_task.done()
            state = "运行中" if running else "已停止"
            await feishu_client.send_text(
                chat_id,
                f"🔈 当前监听状态: {state}\n用法: tg监听 开启 / tg监听 关闭（tg监控 等同）",
            )
            return

        try:
            async with async_session() as db:
                if on:
                    channel_ids = await start_listener_impl(db)
                    await _set_listener_pref(db, True)
                    await feishu_client.send_text(chat_id, f"✅ 监听已开启，监控 {len(channel_ids)} 个频道")
                else:
                    await tg_manager.stop_listener()
                    await _set_listener_pref(db, False)
                    await feishu_client.send_text(chat_id, "⏸ 监听已停止")
        except Exception as e:
            logger.exception("listener toggle failed")
            await feishu_client.send_text(chat_id, f"❌ 操作失败: {e}")

    async def _handle_restart(self, chat_id: str):
        """tg重启 / tg重启系统 — exit the backend process; Docker auto-restarts."""
        await feishu_client.send_text(chat_id, "🔄 正在重启系统，约 10~30 秒后恢复…")
        import os
        import threading

        def _delayed_exit(delay: float = 1.5):
            time.sleep(delay)
            logger.warning("Restart triggered via Feishu command — exiting process, Docker will auto-restart.")
            os._exit(0)

        threading.Thread(target=_delayed_exit, daemon=True).start()

    # ------------------------------------------------------------------
    #  TG re-login flow (driven entirely from Feishu)
    # ------------------------------------------------------------------

    async def _handle_login(self, chat_id: str, args: list[str]):
        """tg登录 <手机号> — send login code via Telegram."""
        if not args:
            await feishu_client.send_text(chat_id, "❌ 用法: tg登录 <手机号>，如 tg登录 +8613800138000")
            return
        phone = args[0].strip()
        from app.services.telegram_client import tg_manager

        try:
            await tg_manager.start_login(phone)
            self._pending_login_phone = phone
            self._pending_login_code = ""
            await feishu_client.send_text(chat_id, f"✅ 验证码已发送至 {phone}\n请回复：tg验证码 <6位验证码>")
        except Exception as e:
            logger.exception("tg login start failed")
            await feishu_client.send_text(chat_id, f"❌ 发送验证码失败: {e}")

    async def _handle_code(self, chat_id: str, args: list[str]):
        """tg验证码 <code> — complete login (or prompt for 2FA password)."""
        if not args:
            await feishu_client.send_text(chat_id, "❌ 用法: tg验证码 <6位验证码>")
            return
        code = args[0].strip()
        phone = self._pending_login_phone
        if not phone:
            await feishu_client.send_text(chat_id, "❌ 请先发送 tg登录 <手机号> 获取验证码")
            return
        from app.services.telegram_client import tg_manager
        from app.core.database import async_session
        from app.api.telegram import reload_listener_impl

        try:
            res = await tg_manager.complete_login(phone, code)
            if res.get("success"):
                self._pending_login_phone = ""
                self._pending_login_code = ""
                try:
                    async with async_session() as db:
                        await reload_listener_impl(db)
                except Exception:
                    logger.warning("reload after login failed")
                await feishu_client.send_text(chat_id, "✅ 登录成功，实时监听已重载")
            elif res.get("need_password"):
                self._pending_login_code = code
                await feishu_client.send_text(chat_id, "🔐 需要两步验证密码，请回复：tg密码 <密码>")
            else:
                await feishu_client.send_text(chat_id, f"❌ 登录失败: {res.get('message')}")
        except Exception as e:
            logger.exception("tg code failed")
            await feishu_client.send_text(chat_id, f"❌ 登录失败: {e}")

    async def _handle_password(self, chat_id: str, args: list[str]):
        """tg密码 <密码> — finish 2FA login, then reload the listener."""
        if not args:
            await feishu_client.send_text(chat_id, "❌ 用法: tg密码 <两步验证密码>")
            return
        pwd = " ".join(args).strip()
        phone = self._pending_login_phone
        if not phone:
            await feishu_client.send_text(chat_id, "❌ 请先发送 tg登录 <手机号> 获取验证码")
            return
        from app.services.telegram_client import tg_manager
        from app.core.database import async_session
        from app.api.telegram import reload_listener_impl

        try:
            res = await tg_manager.complete_login(phone, self._pending_login_code, pwd)
            if res.get("success"):
                self._pending_login_phone = ""
                self._pending_login_code = ""
                try:
                    async with async_session() as db:
                        await reload_listener_impl(db)
                except Exception:
                    logger.warning("reload after 2FA login failed")
                await feishu_client.send_text(chat_id, "✅ 登录成功（含两步验证），实时监听已重载")
            else:
                await feishu_client.send_text(chat_id, f"❌ 登录失败: {res.get('message')}")
        except Exception as e:
            logger.exception("tg password failed")
            await feishu_client.send_text(chat_id, f"❌ 登录失败: {e}")

    # ------------------------------------------------------------------
    #  Catch-up interval setting
    # ------------------------------------------------------------------

    async def _handle_catchup_interval(self, chat_id: str, args: list[str]):
        """tg补抓 <分钟> — set periodic catch-up interval (0 = off)."""
        from app.core.database import async_session
        from app.models import Setting
        from sqlalchemy import select

        if not args:
            async with async_session() as db:
                cur = "2"
                r = await db.execute(select(Setting).where(Setting.key == "catchup_interval_minutes"))
                s = r.scalar_one_or_none()
                if s and s.value:
                    cur = s.value
            await feishu_client.send_text(
                chat_id, f"⏱ 当前补抓间隔: {cur} 分钟（0=关闭）\n用法: tg补抓 <分钟>"
            )
            return

        try:
            minutes = int(args[0].strip())
        except ValueError:
            await feishu_client.send_text(chat_id, "❌ 间隔应为整数分钟（0=关闭）")
            return
        if minutes < 0 or minutes > 180:
            await feishu_client.send_text(chat_id, "❌ 间隔范围 0~180 分钟")
            return

        async with async_session() as db:
            r = await db.execute(select(Setting).where(Setting.key == "catchup_interval_minutes"))
            s = r.scalar_one_or_none()
            if not s:
                s = Setting(key="catchup_interval_minutes", value=str(minutes))
                db.add(s)
            else:
                s.value = str(minutes)
            await db.commit()

        label = "已关闭" if minutes == 0 else f"每 {minutes} 分钟"
        await feishu_client.send_text(chat_id, f"✅ 周期补抓{label}")

    async def _handle_subscribe(self, chat_id: str, args: list[str]):
        """tg订阅 <频道名/@用户名> <群名称> — join a TG channel + bind forward group.

        This is the *root fix* for the "followed but zero realtime forwarding"
        problem: the TG account must actually be a member of the channel for
        Telegram to push realtime updates.  The command joins the channel via
        Telethon and binds it to the named Feishu group in one step.
        """
        if len(args) < 2:
            await feishu_client.send_text(
                chat_id,
                "❌ 用法: tg订阅 <频道名或@用户名> <群名称>\n"
                "例: tg订阅 财联社电报 新闻转发群\n"
                "    tg订阅 @cailianpress 科技快讯",
            )
            return

        channel_arg = args[0].strip()
        group_name = " ".join(args[1:]).strip()

        from app.core.database import async_session
        from app.services import alias_resolver
        from app.services.telegram_client import tg_manager
        from app.api.telegram import reload_listener_impl
        from app.models import Channel

        try:
            async with async_session() as db:
                # 1) Resolve the Feishu group name -> chat id (must already be bound)
                alias = await alias_resolver.resolve_name(db, group_name)
                if not alias:
                    await feishu_client.send_text(
                        chat_id,
                        f"❌ 未找到群「{group_name}」的绑定\n"
                        f"请先用 tg绑定 {group_name} <飞书群ID> 建立绑定",
                    )
                    return
                group_chat_id = alias.feishu_chat_id

                # 2) Find an existing channel (reuse stored id/username), else join fresh
                ch = await self._resolve_channel(db, channel_arg)
                join_arg = channel_arg
                if ch:
                    join_arg = ch.channel_username or ch.channel_id

                # 3) Join the TG channel — this is what enables realtime pushes
                await feishu_client.send_text(
                    chat_id, f"⏳ 正在订阅频道「{channel_arg}」并加入实时监听..."
                )
                res = await tg_manager.join_channel(join_arg)
                if not res.get("ok"):
                    await feishu_client.send_text(chat_id, f"❌ {res.get('message')}")
                    return

                # 4) Create or update the channel row, bound to the group
                if ch:
                    ch.feishu_chat_id = group_chat_id
                    ch.enabled = True
                    title = ch.channel_title or res.get("title") or channel_arg
                else:
                    ch = Channel(
                        channel_id=res["channel_id"],
                        channel_title=res.get("title"),
                        channel_username=res.get("username"),
                        feishu_chat_id=group_chat_id,
                        enabled=True,
                    )
                    db.add(ch)
                    title = res.get("title") or channel_arg
                await db.commit()
                await reload_listener_impl(db)

            await feishu_client.send_text(
                chat_id,
                f"✅ 已订阅频道「{title}」并绑定转发到群「{group_name}」\n"
                "🔔 实时监听已开启，新消息将自动推送（周期补抓仍作兜底）",
            )
        except Exception as e:
            logger.exception("tg subscribe failed")
            await feishu_client.send_text(chat_id, f"❌ 订阅失败: {e}")

    async def _send_help(self, chat_id: str):
        """Send the help message."""
        help_text = (
            "📋 青鸟采集命令\n\n"
            "💡 所有命令以 tg 为前缀，前缀大小写随意（TG帮助 / tg 帮助 / Tg帮助 均可）\n\n"
            "【采集】\n"
            "· tg采集 — 采集所有频道的未转发消息\n"
            "· tg采集 <频道名> — 采集指定频道（标题/用户名模糊匹配）\n"
            "· tg采集 <群名称> — 按群绑定采集（如: tg采集 科技）\n"
            "· tg采集 <日期> — 按日期采集所有频道（YYYY-MM-DD 或 MM-DD）\n"
            "· tg采集 <开始> <结束> — 按日期范围采集\n"
            "· tg采集 <名称> <日期> — 按日期采集指定频道/群\n\n"
            "【频道管理】\n"
            "· tg订阅 <频道名> <群名称> — 加入并订阅频道 + 绑定转发群（根治零转发）\n"
            "· tg暂停 <频道名> — 暂停该频道转发\n"
            "· tg恢复 <频道名> — 恢复已暂停的频道\n"
            "· tg移除 <频道名> — 从系统中删除频道\n\n"
            "【监听与系统】\n"
            "· tg监听 / tg监控 — 查看实时监听状态\n"
            "· tg监听 开启 / tg监听 关闭 — 开启/关闭实时监听（tg监控 等同）\n"
            "· tg重启 — 重启后端服务（Docker 自动拉起）\n"
            "· tg补抓 <分钟> — 周期补抓间隔（0=关闭，默认 2 分钟；无参数查看当前值）\n\n"
            "【Telegram 登录（掉线重登）】\n"
            "· tg登录 <手机号> — 发送登录验证码到 TG（如: tg登录 +8613800138000）\n"
            "· tg验证码 <6位码> — 输入验证码完成登录\n"
            "· tg密码 <两步验证密码> — 2FA 密码登录（验证码后提示需要时使用）\n\n"
            "【其他】\n"
            "· tg绑定 <名称> <群ID> — 绑定群名到飞书群\n"
            "· tg群列表 — 查看所有群名称绑定\n"
            "· tg状态 [群名称] — 发送运行状态报告（默认发到当前群）\n"
            "· tg帮助 — 显示此帮助信息\n\n"
            "⚠️ Telegram 连接断开会主动推送到飞书，届时可按上方「登录」流程重登。"
        )
        await feishu_client.send_text(chat_id, help_text)


# Singleton
feishu_command = FeishuCommandService()
