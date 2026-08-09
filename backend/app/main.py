"""FastAPI application entry point.

Wires up all API routers and handles startup/shutdown lifecycle.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.config import sync_db_settings_to_config
from app.core.database import init_db
from app.core.security import ensure_jwt_secret, decode_token, get_jwt_secret
from app.api import auth, channels, rules, logs, settings as settings_api, stats, telegram, system, aliases

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Paths that don't require authentication
_OPEN_EXACT = {
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/system/health",
    # Auth endpoints that work without a JWT
    "/api/auth/login",
    "/api/auth/otp-verify",
    "/api/auth/setup",
    "/api/auth/status",
    "/api/auth/logout",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    logger.info("Starting %s ...", settings.app_name)
    await init_db()
    logger.info("Database initialized")

    # Load DB-stored settings (proxy, feishu creds, etc.) into the config singleton
    await sync_db_settings_to_config()
    logger.info("Config synced from DB")

    # Ensure JWT secret exists (generate & persist if first run)
    await ensure_jwt_secret()
    logger.info("JWT secret ready")

    # Ensure media cache dir exists
    import os
    os.makedirs(settings.media_cache_dir, exist_ok=True)

    # Start Feishu long-connection command listener
    from app.services.feishu_command import feishu_command
    feishu_command.start()
    logger.info("Feishu command service started")

    # Start periodic status-report scheduler
    from app.services.status_reporter import report_scheduler
    report_scheduler.start()

    # Start periodic catch-up sweep + TG health watchdog
    from app.services.catchup_scheduler import catchup_scheduler
    catchup_scheduler.start()

    # Auto-start the TG listener unless the user explicitly turned it off.
    # Default = ON ("监听运行是自动打开状态"); the dashboard toggle persists
    # the preference to the `listener_enabled` setting.
    asyncio.create_task(_auto_start_listener())

    yield

    # Shutdown
    logger.info("Shutting down ...")
    from app.services.telegram_client import tg_manager
    from app.services.feishu_client import feishu_client
    await feishu_command.stop()
    await report_scheduler.stop()
    await catchup_scheduler.stop()
    await tg_manager.disconnect()
    await feishu_client.close()
    logger.info("Shutdown complete")


async def _auto_start_listener():
    """Auto-start the TG listener on boot (unless the user turned it off)."""
    import asyncio as _asyncio
    from sqlalchemy import select as _select
    from app.core.database import async_session
    from app.models import Setting
    from app.services.telegram_client import tg_manager

    # Give the app a moment to settle before touching TG
    await _asyncio.sleep(3)

    try:
        async with async_session() as db:
            result = await db.execute(
                _select(Setting).where(Setting.key == "listener_enabled")
            )
            pref = result.scalar_one_or_none()
            if pref and pref.value == "false":
                logger.info("Listener auto-start skipped (user preference: off)")
                return

            if not tg_manager.is_connected:
                try:
                    if not await tg_manager.connect():
                        logger.info("Listener auto-start skipped (TG not authorized)")
                        return
                except Exception as e:
                    logger.warning("Listener auto-start: TG connect failed: %s", e)
                    return

            from app.api.telegram import start_listener_impl
            channel_ids = await start_listener_impl(db)
            logger.info("Listener auto-started for %d channels", len(channel_ids))
    except Exception as e:
        logger.warning("Listener auto-start failed: %s", e)


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the frontend container to access the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  Auth middleware — protects all /api routes except auth & health
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Allow open paths without authentication
    if path in _OPEN_EXACT:
        return await call_next(request)

    # Only protect /api/* routes
    if not path.startswith("/api"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = decode_token(token)
            if payload.get("type") == "access":
                request.state.user_id = payload.get("sub")
                request.state.username = payload.get("username")
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

    if not getattr(request.state, "user_id", None):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    return await call_next(request)


# Register routers
app.include_router(auth.router)
app.include_router(system.router)
app.include_router(channels.router)
app.include_router(rules.router)
app.include_router(logs.router)
app.include_router(stats.router)
app.include_router(settings_api.router)
app.include_router(telegram.router)
app.include_router(aliases.router)


@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "api": "/api",
    }
