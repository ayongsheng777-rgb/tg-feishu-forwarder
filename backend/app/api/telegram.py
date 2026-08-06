"""Telegram session & listener management API."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Channel
from app.services.telegram_client import tg_manager
from app.services.forwarder import forwarder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class LoginStartRequest(BaseModel):
    phone: str


class LoginCompleteRequest(BaseModel):
    phone: str
    code: str
    password: str = ""


class ApiCredentialsRequest(BaseModel):
    api_id: int
    api_hash: str


# ------------------------------------------------------------------
#  API credential management
# ------------------------------------------------------------------

@router.post("/api-credentials")
async def set_api_credentials(req: ApiCredentialsRequest, db: AsyncSession = Depends(get_db)):
    """Set TG API ID/Hash at runtime and persist to DB."""
    from app.core.config import settings, sync_db_settings_to_config
    from app.models import Setting

    # Persist to DB settings table
    for key, value in [("tg_api_id", str(req.api_id)), ("tg_api_hash", req.api_hash)]:
        result = await db.execute(select(Setting).where(Setting.key == key))
        s = result.scalar_one_or_none()
        if not s:
            s = Setting(key=key, value=value)
            db.add(s)
        else:
            s.value = value
    await db.commit()

    # Sync to runtime config
    await sync_db_settings_to_config()

    # Force client re-creation on next connect
    if tg_manager._client is not None:
        await tg_manager.disconnect()
        tg_manager._client = None
    return {"ok": True, "api_id": req.api_id}


# ------------------------------------------------------------------
#  Session status & login
# ------------------------------------------------------------------

@router.get("/status")
async def tg_status():
    """Get TG connection and login status."""
    try:
        return await tg_manager.get_login_status()
    except Exception as e:
        return {"authorized": False, "connected": False, "error": str(e), "user": None}


@router.post("/login/start")
async def login_start(req: LoginStartRequest):
    """Send a login code to the phone number."""
    try:
        result = await tg_manager.start_login(req.phone)
        return {"ok": True, "status": result}
    except Exception as e:
        raise HTTPException(500, f"Login start failed: {e}")


@router.post("/login/complete")
async def login_complete(req: LoginCompleteRequest):
    """Complete login with code + optional 2FA password."""
    try:
        result = await tg_manager.complete_login(req.phone, req.code, req.password)
        return result
    except Exception as e:
        raise HTTPException(500, f"Login complete failed: {e}")


@router.post("/logout")
async def logout():
    """Log out and delete the session."""
    try:
        client = tg_manager._get_client()
        if client and client.is_connected():
            await client.log_out()
        else:
            await tg_manager.disconnect()
        tg_manager._client = None
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Logout failed: {e}")


# ------------------------------------------------------------------
#  Listener control
# ------------------------------------------------------------------

async def _set_listener_pref(db: AsyncSession, enabled: bool):
    """Persist the user's desired listener state (survives restarts)."""
    from app.models import Setting

    result = await db.execute(select(Setting).where(Setting.key == "listener_enabled"))
    s = result.scalar_one_or_none()
    value = "true" if enabled else "false"
    if not s:
        db.add(Setting(key="listener_enabled", value=value))
    else:
        s.value = value
    await db.commit()


async def start_listener_impl(db: AsyncSession) -> list[str]:
    """Start the TG listener for all enabled channels. Returns channel IDs."""
    result = await db.execute(
        select(Channel).where(Channel.enabled == True)
    )
    channels = result.scalars().all()
    channel_ids = [ch.channel_id for ch in channels]

    if not channel_ids:
        raise HTTPException(400, "No enabled channels to monitor")

    # Stop existing listener if any
    await tg_manager.stop_listener()

    # Start new listener as background task
    async def _on_message(event):
        from app.core.database import async_session
        async with async_session() as session:
            await forwarder.handle_new_message(session, event)

    tg_manager._listener_task = asyncio.create_task(
        tg_manager.start_listener(channel_ids, _on_message)
    )
    return channel_ids


@router.post("/listener/start")
async def start_listener(db: AsyncSession = Depends(get_db)):
    """Start listening for new messages on all enabled channels."""
    try:
        channel_ids = await start_listener_impl(db)
        await _set_listener_pref(db, True)
        return {"ok": True, "channels": channel_ids}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to start listener: {e}")


@router.post("/listener/stop")
async def stop_listener(db: AsyncSession = Depends(get_db)):
    """Stop the TG message listener."""
    try:
        await tg_manager.stop_listener()
        await _set_listener_pref(db, False)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Failed to stop listener: {e}")


@router.get("/listener/status")
async def listener_status():
    """Check if the listener is running."""
    task = tg_manager._listener_task
    running = task is not None and not task.done()
    return {"running": running}
