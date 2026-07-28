"""Celery tasks for async media download / Feishu upload.

These tasks run in the Celery worker process. They use asyncio.run()
to bridge async Feishu client calls into the sync Celery execution model.
"""
import asyncio
import os
import logging

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def upload_and_send_media(self, file_path: str, media_type: str, feishu_chat_id: str,
                          caption: str = "", channel_title: str = "", sender: str = ""):
    """Download-complete media: upload to Feishu and send message.

    This is called after the backend has already downloaded the media file
    from Telegram. It handles the potentially slow Feishu upload + send,
    with automatic retry on failure.
    """
    try:
        if not os.path.exists(file_path):
            logger.error("Media file not found: %s", file_path)
            return {"success": False, "error": "file_not_found"}

        result = asyncio.run(_do_upload_and_send(file_path, media_type, feishu_chat_id, caption, channel_title, sender))

        # Cleanup the temp file after successful send
        if result.get("success"):
            try:
                os.remove(file_path)
            except Exception:
                pass

        return result

    except Exception as e:
        logger.exception("Media upload task failed: %s", e)
        raise self.retry(exc=e, countdown=30)


async def _do_upload_and_send(file_path: str, media_type: str, feishu_chat_id: str,
                              caption: str, channel_title: str, sender: str) -> dict:
    """Async implementation of media upload + send."""
    from app.services.feishu_client import feishu_client

    feishu_msg_ids = []

    try:
        if media_type == "photo":
            image_key = await feishu_client.upload_image(file_path)
            resp = await feishu_client.send_image(feishu_chat_id, image_key)
            feishu_msg_ids.append(_extract_id(resp))

        elif media_type == "video":
            file_key = await feishu_client.upload_file(file_path, file_type="mp4")
            resp = await feishu_client.send_video(feishu_chat_id, video_key="", file_key=file_key)
            feishu_msg_ids.append(_extract_id(resp))

        else:  # document, sticker, etc.
            file_key = await feishu_client.upload_file(file_path, file_type="stream")
            resp = await feishu_client.send_file(feishu_chat_id, file_key)
            feishu_msg_ids.append(_extract_id(resp))

        # Send caption text if present
        if caption:
            elements = [
                {"tag": "text", "text": f"[{channel_title or 'TG'}] "},
            ]
            if sender:
                elements.append({"tag": "text", "text": f"{sender}:\n"})
            elements.append({"tag": "text", "text": caption})
            resp = await feishu_client.send_rich_text(feishu_chat_id, "", elements)
            feishu_msg_ids.append(_extract_id(resp))

        return {"success": True, "message_ids": feishu_msg_ids}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _extract_id(resp: dict) -> str:
    try:
        return resp.get("data", {}).get("message_id", "")
    except Exception:
        return ""
