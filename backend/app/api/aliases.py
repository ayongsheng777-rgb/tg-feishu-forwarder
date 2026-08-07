"""Chat alias API — manage Feishu group name <-> chat_id bindings."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Channel, ChatAlias
from app.schemas.chat_alias import ChatAliasCreate, ChatAliasOut, ChatAliasUpdate
from app.services import alias_resolver

router = APIRouter(prefix="/api/aliases", tags=["aliases"])


@router.get("", response_model=list[ChatAliasOut])
async def list_aliases(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatAlias).order_by(ChatAlias.name))
    return result.scalars().all()


@router.post("", response_model=None)
async def create_alias(data: ChatAliasCreate, db: AsyncSession = Depends(get_db)):
    from app.services.feishu_client import feishu_client

    name = data.name.strip()
    chat_id = data.feishu_chat_id.strip()
    if not name or not chat_id:
        raise HTTPException(400, "名称和群ID不能为空")
    result = await db.execute(
        select(ChatAlias).where(ChatAlias.feishu_chat_id == chat_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(409, "该飞书群ID已绑定名称「%s」，请直接编辑" % existing.name)
    alias = await alias_resolver.bind_alias(db, name, chat_id, auto=False)

    # Verify the bot is actually a member of that group
    warning = ""
    try:
        real_name = await feishu_client.get_chat_name(chat_id)
        if real_name:
            warning = f"机器人在群内，群实际名称：{real_name}"
    except Exception:
        warning = "⚠️ 机器人不在该群内（或无法访问），转发会失败——请先把机器人拉进该群"

    return {**ChatAliasOut.model_validate(alias).model_dump(), "warning": warning}


@router.put("/{alias_id}", response_model=ChatAliasOut)
async def update_alias(alias_id: int, data: ChatAliasUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatAlias).where(ChatAlias.id == alias_id))
    alias = result.scalar_one_or_none()
    if not alias:
        raise HTTPException(404, "绑定不存在")
    if data.name is not None:
        alias.name = data.name.strip()
        alias.auto_discovered = False  # user edited → treat as manual
    if data.feishu_chat_id is not None:
        alias.feishu_chat_id = data.feishu_chat_id.strip()
    if data.enabled is not None:
        alias.enabled = data.enabled
    await db.commit()
    await db.refresh(alias)
    return alias


@router.delete("/{alias_id}")
async def delete_alias(alias_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatAlias).where(ChatAlias.id == alias_id))
    alias = result.scalar_one_or_none()
    if not alias:
        raise HTTPException(404, "绑定不存在")
    await db.delete(alias)
    await db.commit()
    return {"ok": True}


@router.get("/{alias_id}/channels")
async def alias_channels(alias_id: int, db: AsyncSession = Depends(get_db)):
    """List the TG channels whose target Feishu group matches this alias."""
    result = await db.execute(select(ChatAlias).where(ChatAlias.id == alias_id))
    alias = result.scalar_one_or_none()
    if not alias:
        raise HTTPException(404, "绑定不存在")
    result = await db.execute(
        select(Channel).where(Channel.feishu_chat_id == alias.feishu_chat_id)
    )
    channels = result.scalars().all()
    return [
        {
            "id": ch.id,
            "channel_title": ch.channel_title,
            "channel_id": ch.channel_id,
            "enabled": ch.enabled,
        }
        for ch in channels
    ]
