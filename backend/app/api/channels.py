"""Channel management API — CRUD + TG dialog discovery."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Channel
from app.schemas.channel import ChannelCreate, ChannelUpdate, ChannelOut
from app.services.telegram_client import tg_manager
from app.services.forwarder import forwarder

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).order_by(Channel.id))
    return result.scalars().all()


@router.post("", response_model=ChannelOut)
async def create_channel(data: ChannelCreate, db: AsyncSession = Depends(get_db)):
    ch = Channel(**data.model_dump())
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    # Hot-reload the realtime listener so the new channel is watched immediately
    try:
        from app.api.telegram import reload_listener_impl
        await reload_listener_impl(db)
    except Exception:
        logger.warning("Failed to reload listener after channel create", exc_info=True)
    return ch


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: int, data: ChannelUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Channel not found")
    # Capture what changed so we only reload when it affects monitoring
    changed = data.model_dump(exclude_unset=True)
    for k, v in changed.items():
        setattr(ch, k, v)
    await db.commit()
    await db.refresh(ch)
    # Reload the listener when enabled-state or the TG channel id changes
    if "enabled" in changed or "channel_id" in changed:
        try:
            from app.api.telegram import reload_listener_impl
            await reload_listener_impl(db)
        except Exception:
            logger.warning("Failed to reload listener after channel update", exc_info=True)
    return ch


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Channel not found")
    await db.delete(ch)
    await db.commit()
    # Hot-reload the listener so the removed channel is dropped immediately
    try:
        from app.api.telegram import reload_listener_impl
        await reload_listener_impl(db)
    except Exception:
        logger.warning("Failed to reload listener after channel delete", exc_info=True)
    return {"ok": True}


@router.get("/dialogs")
async def list_tg_dialogs():
    """List all joined TG dialogs (channels, chats, groups)."""
    try:
        dialogs = await tg_manager.list_dialogs()
        return {"dialogs": dialogs}
    except Exception as e:
        raise HTTPException(500, f"Failed to list dialogs: {e}")


@router.post("/{channel_id}/fetch")
async def fetch_channel_messages(
    channel_id: int,
    limit: int = Query(20, ge=1, le=100, description="最多拉取消息数"),
    db: AsyncSession = Depends(get_db),
):
    """手动采集：从 TG 拉取频道最近未转发的消息并转发到飞书。"""
    try:
        result = await forwarder.manual_fetch(db, channel_id, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(500, f"采集失败: {e}")
