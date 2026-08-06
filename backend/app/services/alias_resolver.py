"""Chat alias resolver — maps human-friendly group/bot names to Feishu chat IDs.

Two recognition paths:

1. **Auto-discovery**: when a command arrives from a Feishu group, the bot
   queries the group's name via the Open API and registers (or refreshes)
   an alias row with ``auto_discovered=True``.
2. **Manual binding**: the user binds a name to a chat_id via the admin UI
   or the ``tg绑定 <名称> <chat_id>`` command. Manual rows win on conflict.

Resolution is fuzzy: exact match first, then case-insensitive substring.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatAlias

logger = logging.getLogger(__name__)


async def list_aliases(db: AsyncSession, only_enabled: bool = True) -> list[ChatAlias]:
    stmt = select(ChatAlias).order_by(ChatAlias.name)
    if only_enabled:
        stmt = stmt.where(ChatAlias.enabled == True)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def resolve_name(db: AsyncSession, name: str) -> ChatAlias | None:
    """Resolve a group/bot name to a ChatAlias row. Exact match, then fuzzy."""
    name = (name or "").strip()
    if not name:
        return None

    aliases = await list_aliases(db)
    # Exact (case-insensitive)
    for a in aliases:
        if a.name.lower() == name.lower():
            return a
    # Substring both ways — "IT穿梭机" matches alias "穿梭机" and vice versa
    lowered = name.lower()
    for a in aliases:
        an = a.name.lower()
        if an and (an in lowered or lowered in an):
            return a
    return None


async def bind_alias(db: AsyncSession, name: str, chat_id: str, auto: bool = False) -> ChatAlias:
    """Create or update an alias binding. Manual binding clears auto flag."""
    result = await db.execute(
        select(ChatAlias).where(ChatAlias.feishu_chat_id == chat_id)
    )
    alias = result.scalar_one_or_none()
    if alias:
        alias.name = name
        if not auto:
            alias.auto_discovered = False
        alias.enabled = True
    else:
        alias = ChatAlias(name=name, feishu_chat_id=chat_id, auto_discovered=auto, enabled=True)
        db.add(alias)
    await db.commit()
    await db.refresh(alias)
    return alias


async def auto_discover(db: AsyncSession, chat_id: str) -> ChatAlias | None:
    """Fetch a group's name from Feishu and register it as an alias.

    Skips groups that already have a *manual* binding (never overwrites
    user-defined names). Returns the alias row or None on failure.
    """
    if not chat_id:
        return None

    result = await db.execute(
        select(ChatAlias).where(ChatAlias.feishu_chat_id == chat_id)
    )
    existing = result.scalar_one_or_none()
    if existing and not existing.auto_discovered:
        return existing  # manual binding — respect it

    try:
        from app.services.feishu_client import feishu_client
        name = await feishu_client.get_chat_name(chat_id)
    except Exception as e:
        logger.debug("auto_discover: could not fetch chat name for %s: %s", chat_id[:12], e)
        return existing

    if not name:
        return existing

    if existing:
        if existing.name != name:
            existing.name = name
            await db.commit()
            await db.refresh(existing)
        return existing

    return await bind_alias(db, name, chat_id, auto=True)
