"""Celery tasks for retrying failed forwards."""
import asyncio
import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def retry_forward(self, log_id: int, channel_id: int, tg_message_id: int,
                  feishu_chat_id: str, text: str, media_type: str):
    """Retry a previously failed forward.

    Re-attempts the Feishu send for a logged message.
    """
    try:
        result = asyncio.run(_do_retry(feishu_chat_id, text, media_type))
        return result
    except Exception as e:
        logger.exception("Retry forward failed for log %s", log_id)
        raise self.retry(exc=e)


async def _do_retry(feishu_chat_id: str, text: str, media_type: str) -> dict:
    from app.services.feishu_client import feishu_client

    if media_type and media_type != "text":
        # For media retries, we'd need to re-download from TG.
        # For now, send the text portion if available.
        if text:
            resp = await feishu_client.send_text(feishu_chat_id, text)
            return {"success": True, "message_id": resp.get("data", {}).get("message_id", "")}
        return {"success": False, "error": "cannot_retry_media_without_download"}

    resp = await feishu_client.send_text(feishu_chat_id, text)
    return {"success": True, "message_id": resp.get("data", {}).get("message_id", "")}
