"""System health & info API."""
import os
import threading
import time
import logging
from fastapi import APIRouter

from app.core.config import settings
from app.services.telegram_client import tg_manager

router = APIRouter(prefix="/api/system", tags=["system"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "debug": settings.debug,
    }


@router.get("/info")
async def system_info():
    """Return system configuration overview (secrets masked)."""
    return {
        "tg_configured": bool(settings.tg_api_id and settings.tg_api_hash),
        "feishu_configured": bool(settings.feishu_app_id and settings.feishu_app_secret),
        "proxy_configured": bool(settings.proxy_url),
        "redis_url": settings.redis_url,
        "media_cache_dir": settings.media_cache_dir,
        "tg_connected": tg_manager.is_connected,
    }


@router.get("/env")
async def env_info():
    """Return raw environment settings for debugging."""
    return {
        "TG_API_ID": settings.tg_api_id,
        "TG_API_HASH": "***" if settings.tg_api_hash else "",
        "FEISHU_APP_ID": settings.feishu_app_id,
        "FEISHU_APP_SECRET": "***" if settings.feishu_app_secret else "",
        "PROXY_URL": settings.proxy_url,
        "REDIS_URL": settings.redis_url,
        "DB_URL": settings.db_url,
    }


# ------------------------------------------------------------------
#  Runtime status report
# ------------------------------------------------------------------

@router.get("/report")
async def report_preview():
    """Build the status report and return it as text (no sending)."""
    from app.core.database import async_session
    from app.services.status_reporter import build_report

    async with async_session() as db:
        text = await build_report(db)
    return {"ok": True, "text": text}


@router.post("/report/send")
async def report_send(body: dict | None = None):
    """Send the status report to a Feishu group.

    Body (optional): {"chat_id": "oc_..."} — overrides the configured
    ``report_chat_id`` setting.
    """
    from app.services.status_reporter import send_report

    chat_id = (body or {}).get("chat_id") or None
    try:
        result = await send_report(chat_id)
    except Exception as e:
        return {"success": False, "message": str(e)}
    return result


def _delayed_exit(delay: float = 1.5):
    """Wait briefly then exit the process; Docker restart policy brings it back."""
    time.sleep(delay)
    logger.warning("Restart triggered by user — exiting process, Docker will auto-restart.")
    os._exit(0)


@router.post("/restart")
async def restart_service():
    """Restart the backend container.

    Spawns a daemon thread that exits the process after a short delay,
    giving the HTTP response time to flush. Docker's ``restart: always``
    policy automatically restarts the container.
    """
    t = threading.Thread(target=_delayed_exit, daemon=True)
    t.start()
    return {"status": "restarting", "message": "Service is restarting, please wait..."}
