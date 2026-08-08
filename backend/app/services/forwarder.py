"""Core forwarder — orchestrates TG → rule evaluation → Feishu send.

For text-only messages, sending happens inline (fast).
For messages with media, a Celery task is dispatched for async download + upload.
"""
import logging
import os
import re
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ForwardLog
from app.services.rule_engine import rule_engine
from app.services.feishu_client import feishu_client, FeishuNotInChatError
from app.services.telegram_client import tg_manager
from app.core.config import settings

logger = logging.getLogger(__name__)

# 视频不转发（飞书对 TG 视频编码/体积限制严格，且大文件下载会拖死补抓循环），
# 改为投递文案 + 本提示。
VIDEO_NOTICE = "📹 此信息包含视频，请回 TG 观看原视频！"


class Forwarder:
    """Main forwarding orchestration."""

    async def handle_new_message(self, db: AsyncSession, event):
        """Process a new TG message event."""
        chat_id = str(event.chat_id)
        msg = event.message

        # Find the channel config for this chat
        result = await db.execute(
            select(Channel).where(Channel.channel_id == chat_id, Channel.enabled == True)
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return  # Channel not configured or disabled

        # Dedup: skip already-processed messages
        if msg.id <= channel.last_forwarded_id:
            return

        # Determine media type
        media_type = await rule_engine.get_media_type(event)
        sender_name = await self._get_sender_name(event)

        # Extract text (caption for media messages)
        text = msg.message or msg.text or ""

        # Clean text
        text = self._clean_text(text)

        # Evaluate rules
        should_forward, reason = await rule_engine.evaluate(
            db, channel.id, text, media_type
        )

        if not should_forward:
            await self._log(db, channel.id, msg.id, sender_name, text, "", "skipped", "", media_type, f"Filtered: {reason}")
            await self._update_last_id(db, channel.id, msg.id)
            return

        # Get Feishu target
        feishu_chat = channel.feishu_chat_id
        if not feishu_chat:
            await self._log(db, channel.id, msg.id, sender_name, text, "", "failed", "", media_type, "No Feishu chat_id configured")
            await self._update_last_id(db, channel.id, msg.id)
            return

        # Forward based on media type
        try:
            if media_type == "text":
                feishu_msg_id = await self._forward_text(feishu_chat, channel, sender_name, text)
            elif media_type == "photo":
                feishu_msg_id = await self._forward_photo(feishu_chat, channel, sender_name, text, event, msg)
            elif media_type == "video":
                feishu_msg_id = await self._forward_video(feishu_chat, channel, sender_name, text, event, msg)
            elif media_type in ("document", "sticker", "voice"):
                feishu_msg_id = await self._forward_document(feishu_chat, channel, sender_name, text, event, msg)
            else:
                feishu_msg_id = await self._forward_text(feishu_chat, channel, sender_name, text or "[Unsupported message type]")

            await self._log(db, channel.id, msg.id, sender_name, text, feishu_msg_id, "success", "", media_type, "")
            await self._update_last_id(db, channel.id, msg.id)

        except FeishuNotInChatError as e:
            # 永久错误：机器人不在目标群，需手动拉入。记 skipped 并推进游标，
            # 避免补抓每 2 分钟无限重试刷屏。
            logger.warning("Forward skipped (bot not in chat %s): %s", feishu_chat, e)
            await self._log(
                db, channel.id, msg.id, sender_name, text, "",
                "skipped", "", media_type,
                f"机器人不在目标群({feishu_chat})，请将飞书机器人加入该群后重试",
            )
            await self._update_last_id(db, channel.id, msg.id)
        except Exception as e:
            logger.exception("Forward failed for msg %s in channel %s", msg.id, chat_id)
            await self._log(db, channel.id, msg.id, sender_name, text, "", "failed", "", media_type, str(e))
            # NOTE: do NOT advance last_forwarded_id on failure — a later
            # manual fetch (采集) will retry this message automatically.

    # ------------------------------------------------------------------
    #  Individual forward methods
    # ------------------------------------------------------------------

    async def _forward_text(self, chat_id: str, channel: Channel, sender: str, text: str) -> str:
        """Send a text message to Feishu."""
        # Build a rich-text post with sender info
        elements = [
            {"tag": "text", "text": f"[{channel.channel_title or 'TG'}] "},
        ]
        if sender:
            elements.append({"tag": "text", "text": f"{sender}:\n"})
        elements.append({"tag": "text", "text": text})

        resp = await feishu_client.send_rich_text(chat_id, "", elements)
        return self._extract_msg_id(resp)

    async def _forward_photo(self, chat_id: str, channel: Channel, sender: str, text: str, event, msg) -> str:
        """Download photo from TG, upload to Feishu, send image + caption."""
        # Download
        file_path = await tg_manager.download_media(msg)
        if not file_path:
            # Fallback: send text only
            return await self._forward_text(chat_id, channel, sender, text)

        try:
            # Upload to Feishu
            image_key = await feishu_client.upload_image(file_path)
            # Send image message
            resp = await feishu_client.send_image(chat_id, image_key)

            # If there's a caption, send it as a separate text message
            if text:
                await self._forward_text(chat_id, channel, sender, text)

            return self._extract_msg_id(resp)
        finally:
            self._cleanup(file_path)

    async def _forward_video(self, chat_id: str, channel: Channel, sender: str, text: str, event, msg) -> str:
        """Videos are intentionally NOT forwarded.

        Feishu rejects most TG videos (codec/size limits) and downloading large
        files blocks the catch-up loop for minutes. Deliver the caption plus a
        notice pointing back to Telegram instead.
        """
        return await self._forward_text(
            chat_id, channel, sender, self._video_notice_text(channel, text, msg)
        )

    @staticmethod
    def _tg_message_link(channel: Channel, msg) -> str:
        """Best-effort t.me deep link for a channel message."""
        mid = getattr(msg, "id", None)
        if not mid:
            return ""
        username = (channel.channel_username or "").lstrip("@")
        if username:
            return f"https://t.me/{username}/{mid}"
        cid = str(channel.channel_id or "")
        if cid.startswith("-100"):
            return f"https://t.me/c/{cid[4:]}/{mid}"
        return ""

    def _video_notice_text(self, channel: Channel, text: str, msg) -> str:
        """Caption + 'watch on Telegram' notice used in place of the video."""
        parts = []
        if text:
            parts.append(text)
        parts.append(VIDEO_NOTICE)
        link = self._tg_message_link(channel, msg)
        if link:
            parts.append(link)
        return "\n\n".join(parts)

    async def _forward_document(self, chat_id: str, channel: Channel, sender: str, text: str, event, msg) -> str:
        """Download document from TG, upload to Feishu, send file + caption."""
        file_path = await tg_manager.download_media(msg)
        if not file_path:
            return await self._forward_text(chat_id, channel, sender, text)

        try:
            file_key = await feishu_client.upload_file(file_path, file_type="stream")
            resp = await feishu_client.send_file(chat_id, file_key)

            if text:
                await self._forward_text(chat_id, channel, sender, text)

            return self._extract_msg_id(resp)
        finally:
            self._cleanup(file_path)

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    async def _get_sender_name(self, event) -> str:
        try:
            sender = await event.get_sender()
            if sender:
                name = sender.first_name or ""
                if sender.last_name:
                    name += f" {sender.last_name}"
                if sender.username:
                    name += f" (@{sender.username})"
                return name.strip()
        except Exception:
            pass
        return ""

    def _clean_text(self, text: str) -> str:
        """Clean up TG message text for Feishu."""
        if not text:
            return ""
        # Convert TG entity formatting to plain text
        # (Telethon already provides plain text via .message/.text)
        # Trim excessive whitespace
        lines = text.split("\n")
        cleaned = "\n".join(line.rstrip() for line in lines)
        return cleaned.strip()

    def _extract_msg_id(self, resp: dict) -> str:
        """Extract the message_id from a Feishu API response."""
        try:
            return resp.get("data", {}).get("message_id", "")
        except Exception:
            return ""

    def _cleanup(self, file_path: str | None):
        """Delete a temporary media file."""
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                logger.warning("Failed to cleanup file: %s", file_path)

    async def _log(self, db: AsyncSession, channel_id, tg_msg_id, sender, text, feishu_msg_id, status, error, media_type, extra=""):
        """Insert a forward log entry."""
        log = ForwardLog(
            channel_id=channel_id,
            tg_message_id=tg_msg_id,
            tg_sender=sender,
            message_text=(text or "")[:2000],
            feishu_message_id=feishu_msg_id,
            status=status,
            error_message=(error + (f" | {extra}" if extra else "")) or None,
            media_type=media_type,
        )
        db.add(log)
        await db.commit()

    async def _update_last_id(self, db: AsyncSession, channel_id: int, msg_id: int):
        """Update the last forwarded message ID for dedup."""
        await db.execute(
            update(Channel).where(Channel.id == channel_id).values(last_forwarded_id=msg_id)
        )
        await db.commit()


    async def retry_forward(self, db: AsyncSession, log_id: int) -> dict:
        """Re-process a failed forward log entry.

        Fetches the original TG message by ID and re-runs the full pipeline.
        """
        from app.services.telegram_client import tg_manager

        result = await db.execute(select(ForwardLog).where(ForwardLog.id == log_id))
        log = result.scalar_one_or_none()
        if not log:
            return {"success": False, "message": "Log entry not found"}
        if log.status == "success":
            return {"success": False, "message": "Already forwarded successfully"}

        result = await db.execute(select(Channel).where(Channel.id == log.channel_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return {"success": False, "message": "Channel not found"}
        if not channel.feishu_chat_id:
            return {"success": False, "message": "No Feishu chat_id configured"}

        client = tg_manager._get_client()
        if not client.is_connected():
            await client.connect()

        # Fetch the original message from TG
        try:
            tg_msgs = await client.get_messages(int(channel.channel_id), ids=[log.tg_message_id])
            msg = tg_msgs[0] if tg_msgs else None
        except Exception as e:
            return {"success": False, "message": f"Failed to fetch TG message: {e}"}

        if not msg:
            return {"success": False, "message": "Message not found in TG (may have been deleted)"}

        text = self._clean_text(msg.message or msg.text or "")
        media_type = rule_engine._get_media_type_from_msg(msg)

        try:
            if media_type == "text":
                feishu_msg_id = await self._forward_text(channel.feishu_chat_id, channel, log.tg_sender, text)
            elif media_type == "photo":
                feishu_msg_id = await self._forward_photo_retry(channel.feishu_chat_id, channel, log.tg_sender, text, msg)
            elif media_type == "video":
                feishu_msg_id = await self._forward_video_retry(channel.feishu_chat_id, channel, log.tg_sender, text, msg)
            else:
                feishu_msg_id = await self._forward_document_retry(channel.feishu_chat_id, channel, log.tg_sender, text, msg)

            # Update existing log entry
            log.feishu_message_id = feishu_msg_id
            log.status = "success"
            log.error_message = None
            await db.commit()
            return {"success": True, "message": "Retry succeeded", "feishu_msg_id": feishu_msg_id}

        except FeishuNotInChatError as e:
            # 永久错误：机器人不在目标群，无需重试。记 skipped 并推进游标。
            logger.warning("Retry skipped (bot not in chat %s): %s", channel.feishu_chat_id, e)
            log.status = "skipped"
            log.error_message = f"机器人不在目标群({channel.feishu_chat_id})，请将飞书机器人加入该群后重试"
            await db.commit()
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.exception("Retry failed for log %s", log_id)
            log.status = "failed"
            log.error_message = str(e)
            await db.commit()
            return {"success": False, "message": str(e)}

    async def _forward_photo_retry(self, chat_id, channel, sender, text, msg) -> str:
        """Retry photo forward using a raw message object."""
        file_path = await tg_manager.download_media(msg)
        if not file_path:
            return await self._forward_text(chat_id, channel, sender, text)
        try:
            image_key = await feishu_client.upload_image(file_path)
            resp = await feishu_client.send_image(chat_id, image_key)
            if text:
                await self._forward_text(chat_id, channel, sender, text)
            return self._extract_msg_id(resp)
        finally:
            self._cleanup(file_path)

    async def _forward_video_retry(self, chat_id, channel, sender, text, msg) -> str:
        """Videos are not forwarded — send caption + notice (see _forward_video)."""
        return await self._forward_text(
            chat_id, channel, sender, self._video_notice_text(channel, text, msg)
        )

    async def _forward_document_retry(self, chat_id, channel, sender, text, msg) -> str:
        file_path = await tg_manager.download_media(msg)
        if not file_path:
            return await self._forward_text(chat_id, channel, sender, text)
        try:
            file_key = await feishu_client.upload_file(file_path, file_type="stream")
            resp = await feishu_client.send_file(chat_id, file_key)
            if text:
                await self._forward_text(chat_id, channel, sender, text)
            return self._extract_msg_id(resp)
        finally:
            self._cleanup(file_path)

    # ------------------------------------------------------------------
    #  Manual fetch — pull missed messages from a TG channel
    # ------------------------------------------------------------------

    async def manual_fetch(self, db: AsyncSession, channel_db_id: int, limit: int = 20) -> dict:
        """Fetch messages after ``last_forwarded_id`` and forward them to Feishu.

        This is used when the listener missed messages (e.g. downtime, reconnect)
        and the user wants to backfill them manually.
        """
        result = await db.execute(select(Channel).where(Channel.id == channel_db_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return {"success": False, "message": "频道不存在"}
        if not channel.feishu_chat_id:
            return {"success": False, "message": "未配置飞书群ID"}

        client = tg_manager._get_client()
        if not client.is_connected():
            await client.connect()

        # Fetch messages whose ID > last_forwarded_id
        min_id = channel.last_forwarded_id or 0
        try:
            messages = await client.get_messages(
                int(channel.channel_id),
                min_id=min_id,
                limit=limit,
            )
        except Exception as e:
            logger.exception("Manual fetch: failed to get messages from %s", channel.channel_id)
            return {"success": False, "message": f"拉取消息失败: {e}"}

        # Sort ascending (oldest first) so messages forward in chronological order
        messages = sorted(messages, key=lambda m: m.id)

        stats = {"fetched": 0, "forwarded": 0, "skipped": 0, "failed": 0, "errors": []}

        for msg in messages:
            # Extra dedup guard
            if msg.id <= min_id:
                continue
            stats["fetched"] += 1

            text = self._clean_text(msg.message or msg.text or "")
            media_type = rule_engine._get_media_type_from_msg(msg)
            sender = await self._get_sender_from_msg(msg)

            # Evaluate rules
            should_forward, reason = await rule_engine.evaluate(
                db, channel.id, text, media_type
            )

            if not should_forward:
                await self._log(db, channel.id, msg.id, sender, text, "", "skipped", "", media_type, f"Filtered: {reason}")
                await self._update_last_id(db, channel.id, msg.id)
                stats["skipped"] += 1
                continue

            try:
                if media_type == "text":
                    feishu_msg_id = await self._forward_text(channel.feishu_chat_id, channel, sender, text)
                elif media_type == "photo":
                    feishu_msg_id = await self._forward_photo_retry(channel.feishu_chat_id, channel, sender, text, msg)
                elif media_type == "video":
                    feishu_msg_id = await self._forward_video_retry(channel.feishu_chat_id, channel, sender, text, msg)
                else:
                    feishu_msg_id = await self._forward_document_retry(channel.feishu_chat_id, channel, sender, text, msg)

                await self._log(db, channel.id, msg.id, sender, text, feishu_msg_id, "success", "", media_type, "")
                await self._update_last_id(db, channel.id, msg.id)
                stats["forwarded"] += 1

            except FeishuNotInChatError as e:
                # 永久错误：机器人不在目标群，无需重试。记 skipped 并推进游标。
                logger.warning(
                    "Manual fetch skipped (bot not in chat %s) for channel %s: %s",
                    channel.feishu_chat_id, channel.channel_id, e,
                )
                await self._log(
                    db, channel.id, msg.id, sender, text, "",
                    "skipped", "", media_type,
                    f"机器人不在目标群({channel.feishu_chat_id})，请将飞书机器人加入该群后重试",
                )
                await self._update_last_id(db, channel.id, msg.id)
                stats["skipped"] += 1
            except Exception as e:
                logger.exception("Manual fetch: forward failed for msg %s in channel %s", msg.id, channel.channel_id)
                await self._log(db, channel.id, msg.id, sender, text, "", "failed", "", media_type, str(e))
                # NOTE: keep last_forwarded_id untouched so the next fetch
                # retries this failed message instead of skipping it.
                stats["failed"] += 1
                if len(stats["errors"]) < 10:
                    stats["errors"].append(f"消息 {msg.id}: {e}")

        logger.info(
            "Manual fetch done for channel %s: fetched=%d forwarded=%d skipped=%d failed=%d",
            channel.channel_title, stats["fetched"], stats["forwarded"], stats["skipped"], stats["failed"],
        )
        return {"success": True, **stats}

    async def _get_sender_from_msg(self, msg) -> str:
        """Extract sender display name from a raw Telethon Message object."""
        try:
            sender = await msg.get_sender()
            if sender:
                name = sender.first_name or ""
                if sender.last_name:
                    name += f" {sender.last_name}"
                if sender.username:
                    name += f" (@{sender.username})"
                return name.strip()
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    #  Date-range fetch — pull messages by date and forward them
    # ------------------------------------------------------------------

    async def fetch_by_date(
        self,
        db: AsyncSession,
        channel_db_id: int,
        start_date,
        end_date,
        limit: int = 200,
    ) -> dict:
        """Fetch messages within a date range and forward them to Feishu.

        ``start_date`` and ``end_date`` are naive ``datetime`` objects in
        local time (UTC+8).  Already-forwarded messages (with a ``success``
        log entry) are skipped to avoid duplicates.
        """
        from datetime import timezone, timedelta

        CST = timezone(timedelta(hours=8))

        result = await db.execute(select(Channel).where(Channel.id == channel_db_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return {"success": False, "message": "频道不存在"}
        if not channel.feishu_chat_id:
            return {"success": False, "message": "未配置飞书群ID"}

        client = tg_manager._get_client()
        if not client.is_connected():
            await client.connect()

        # Convert local dates to UTC for Telethon
        start_utc = start_date.replace(hour=0, minute=0, second=0, tzinfo=CST).astimezone(timezone.utc)
        end_utc = end_date.replace(hour=23, minute=59, second=59, tzinfo=CST).astimezone(timezone.utc)

        # Telethon's offset_date returns messages *before* this datetime
        fetch_offset = end_utc + timedelta(seconds=1)

        try:
            messages = await client.get_messages(
                int(channel.channel_id),
                offset_date=fetch_offset,
                limit=limit,
            )
        except Exception as e:
            logger.exception("fetch_by_date: failed to get messages from %s", channel.channel_id)
            return {"success": False, "message": f"拉取消息失败: {e}"}

        # Filter messages within the date range (compare in UTC)
        filtered = []
        for m in messages:
            if m.date.tzinfo and start_utc <= m.date <= end_utc:
                filtered.append(m)
            elif m.date.tzinfo and m.date < start_utc:
                break  # Messages are reverse-chronological; stop early

        # Sort ascending (oldest first) for chronological forwarding
        filtered = sorted(filtered, key=lambda m: m.id)

        # Check which messages were already forwarded successfully
        msg_ids = [m.id for m in filtered]
        already_done = set()
        if msg_ids:
            result = await db.execute(
                select(ForwardLog.tg_message_id).where(
                    ForwardLog.channel_id == channel.id,
                    ForwardLog.tg_message_id.in_(msg_ids),
                    ForwardLog.status == "success",
                )
            )
            already_done = set(result.scalars().all())

        stats = {"fetched": 0, "forwarded": 0, "skipped": 0, "failed": 0, "errors": []}

        for msg in filtered:
            if msg.id in already_done:
                stats["skipped"] += 1
                continue

            stats["fetched"] += 1
            text = self._clean_text(msg.message or msg.text or "")
            media_type = rule_engine._get_media_type_from_msg(msg)
            sender = await self._get_sender_from_msg(msg)

            should_forward, reason = await rule_engine.evaluate(
                db, channel.id, text, media_type
            )
            if not should_forward:
                await self._log(db, channel.id, msg.id, sender, text, "", "skipped", "", media_type, f"Filtered: {reason}")
                stats["skipped"] += 1
                continue

            try:
                if media_type == "text":
                    feishu_msg_id = await self._forward_text(channel.feishu_chat_id, channel, sender, text)
                elif media_type == "photo":
                    feishu_msg_id = await self._forward_photo_retry(channel.feishu_chat_id, channel, sender, text, msg)
                elif media_type == "video":
                    feishu_msg_id = await self._forward_video_retry(channel.feishu_chat_id, channel, sender, text, msg)
                else:
                    feishu_msg_id = await self._forward_document_retry(channel.feishu_chat_id, channel, sender, text, msg)

                await self._log(db, channel.id, msg.id, sender, text, feishu_msg_id, "success", "", media_type, "")
                stats["forwarded"] += 1

            except Exception as e:
                logger.exception("fetch_by_date: forward failed for msg %s in channel %s", msg.id, channel.channel_id)
                await self._log(db, channel.id, msg.id, sender, text, "", "failed", "", media_type, str(e))
                stats["failed"] += 1
                if len(stats["errors"]) < 10:
                    stats["errors"].append(f"消息 {msg.id}: {e}")

        logger.info(
            "fetch_by_date done for channel %s (%s~%s): fetched=%d forwarded=%d skipped=%d failed=%d",
            channel.channel_title, start_date.date(), end_date.date(),
            stats["fetched"], stats["forwarded"], stats["skipped"], stats["failed"],
        )
        return {"success": True, **stats}


forwarder = Forwarder()
