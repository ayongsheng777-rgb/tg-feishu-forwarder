"""System settings API — key-value store for runtime config.

Stores Feishu credentials, proxy, TG API credentials, etc.
so they can be changed from the admin UI without restarting.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Setting
from app.schemas.setting import SettingOut, SettingUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=list[SettingOut])
async def list_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting).order_by(Setting.key))
    return result.scalars().all()


@router.get("/{key}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting).where(Setting.key == key))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Setting not found")
    return {"key": s.key, "value": s.value}


@router.put("/{key}", response_model=SettingOut)
async def update_setting(key: str, data: SettingUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Setting).where(Setting.key == key))
    s = result.scalar_one_or_none()
    if not s:
        s = Setting(key=key, value=data.value)
        db.add(s)
    else:
        s.value = data.value
    await db.commit()
    await db.refresh(s)

    # Sync the config singleton so runtime code picks up the change
    from app.core.config import sync_db_settings_to_config
    await sync_db_settings_to_config()

    return s


@router.post("/bulk")
async def bulk_update_settings(items: dict[str, str], db: AsyncSession = Depends(get_db)):
    """Update multiple settings at once. Body: {"key1": "value1", "key2": "value2"}"""
    for key, value in items.items():
        result = await db.execute(select(Setting).where(Setting.key == key))
        s = result.scalar_one_or_none()
        if not s:
            s = Setting(key=key, value=value)
            db.add(s)
        else:
            s.value = value
    await db.commit()

    # If Feishu credentials were updated, refresh the runtime client
    if "feishu_app_id" in items or "feishu_app_secret" in items:
        from app.services.feishu_client import feishu_client
        app_id = items.get("feishu_app_id", feishu_client.app_id)
        app_secret = items.get("feishu_app_secret", feishu_client.app_secret)
        feishu_client.update_credentials(app_id, app_secret)

    # Sync the config singleton so runtime code picks up the change
    from app.core.config import sync_db_settings_to_config
    await sync_db_settings_to_config()

    return {"ok": True, "updated": len(items)}
