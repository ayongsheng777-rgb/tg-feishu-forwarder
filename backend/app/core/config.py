"""Application configuration loaded from environment variables."""
import os
import logging
from pydantic_settings import BaseSettings
from pydantic import Field

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # --- General ---
    app_name: str = "青鸟 QingNiao Forwarder"
    debug: bool = Field(default=False, alias="DEBUG")

    # --- Database ---
    db_url: str = Field(default="sqlite+aiosqlite:////data/tg_feishu.db", alias="DB_URL")

    # --- Redis ---
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # --- Proxy (used by Telethon & httpx) ---
    proxy_url: str = Field(default="", alias="PROXY_URL")

    # --- Telegram API credentials ---
    tg_api_id: int = Field(default=0, alias="TG_API_ID")
    tg_api_hash: str = Field(default="", alias="TG_API_HASH")
    tg_session_name: str = Field(default="forwarder_session", alias="TG_SESSION_NAME")

    # --- Feishu Open Platform ---
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")

    # --- Paths ---
    media_cache_dir: str = Field(default="/app/media_cache", alias="MEDIA_CACHE_DIR")

    @property
    def proxy_tuple(self):
        """Return a proxy tuple suitable for Telethon, or None."""
        p = self.proxy_url.strip()
        if not p:
            return None
        # Parse socks5://host:port or http://host:port
        if p.startswith("socks5://"):
            host_port = p[len("socks5://"):]
            scheme = 2  # Telethon SOCKS5
        elif p.startswith("http://"):
            host_port = p[len("http://"):]
            scheme = 3  # Telethon HTTP
        elif p.startswith("https://"):
            host_port = p[len("https://"):]
            scheme = 3
        else:
            return None
        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
            try:
                port = int(port)
            except ValueError:
                return None
            return (scheme, host, port)
        return None

    @property
    def httpx_proxy(self):
        """Return proxy URL for httpx, or None."""
        p = self.proxy_url.strip()
        return p if p else None

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

# Keys in the DB settings table that should override env-var config
_DB_OVERRIDE_KEYS = {
    "proxy_url": "proxy_url",
    "feishu_app_id": "feishu_app_id",
    "feishu_app_secret": "feishu_app_secret",
    "tg_api_id": "tg_api_id",
    "tg_api_hash": "tg_api_hash",
}


async def sync_db_settings_to_config():
    """Load settings from the DB table and override the ``settings`` singleton.

    Called on startup (after ``init_db``) and after every settings save,
    so that values changed via the UI take effect without editing env vars.
    """
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models import Setting

    try:
        async with async_session() as db:
            result = await db.execute(select(Setting))
            rows = {row.key: row.value for row in result.scalars().all()}
    except Exception as e:
        logger.warning("Could not load DB settings: %s", e)
        return

    for db_key, attr_name in _DB_OVERRIDE_KEYS.items():
        val = rows.get(db_key)
        if val is None:
            continue
        if attr_name == "tg_api_id":
            try:
                val = int(val)
            except (ValueError, TypeError):
                continue
        if val != getattr(settings, attr_name):
            setattr(settings, attr_name, val)
            logger.info("Config '%s' overridden from DB", db_key)
