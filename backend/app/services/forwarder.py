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
from app.services.feishu_client import feishu_client
from app.services.telegram_client import tg_manager
from app.core.config import settings

logger = logging.getLogger(__name__)


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

        except Exception as e:
            logger.exception("Forward failed for msg %s in channel %s", msg.id, chat_id)
            await self._log(db, channel.id, msg.id, sender_name, text, "", "failed", "", media_type, str(e))
            await self._update_last_id(db, channel.id, msg.id)

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
        """Download video from TG, upload to Feishu, send video + caption."""
        file_path = await tg_manager.download_media(msg)
        if not file_path:
            return await self._forward_text(chat_id, channel, sender, text)

        try:
            file_key = await feishu_client.upload_file(file_path, file_type="mp4")
            resp = await feishu_client.send_video(chat_id, video_key="", file_key=file_key)

            if text:
                await self._forward_text(chat_id, channel, sender, text)

            return self._extract_msg_id(resp)
        finally:
            self._cleanup(file_path)

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
        file_path = await tg_manager.download_media(msg)
        if not file_path:
            return await self._forward_text(chat_id, channel, sender, text)
        try:
            file_key = await feishu_client.upload_file(file_path, file_type="mp4")
            resp = await feishu_client.send_video(chat_id, video_key="", file_key=file_key)
            if text:
                await self._forward_text(chat_id, channel, sender, text)
            return self._extract_msg_id(resp)
        finally:
            self._cleanup(file_path)

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


forwarder = Forwarder()
