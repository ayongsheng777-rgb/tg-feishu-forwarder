"""SQLAlchemy async engine and session factory."""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.db_url, echo=settings.debug)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine.sync_engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, _connection_record):
    """SQLite 调优（仅对 sqlite:// 生效）。

    - ``journal_mode=WAL``：读写不再互斥。默认的 delete 日志模式下，一个长读
      事务会把所有写入挡住 —— 仪表盘慢查询期间转发日志写不进去，转发会跟着卡。
    - ``synchronous=NORMAL``：WAL 下安全，且比 FULL 明显快。
    - ``busy_timeout``：写冲突时等一会儿，而不是立刻抛 database is locked。
    """
    if not settings.db_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


# 老库升级用：``create_all`` 不会给「已存在的表」补建索引，这里显式补一次。
# 全是 IF NOT EXISTS，重复执行无副作用。
_INDEX_MIGRATIONS = (
    "CREATE INDEX IF NOT EXISTS ix_forward_logs_channel_status_created "
    "ON forward_logs (channel_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_forward_logs_created_at ON forward_logs (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_forward_logs_status ON forward_logs (status)",
)


async def init_db():
    """Create all tables (called on startup)."""
    async with engine.begin() as conn:
        from app.models import channel, rule, forward_log, setting, user, chat_alias  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _INDEX_MIGRATIONS:
            await conn.execute(text(stmt))


async def get_db():
    """FastAPI dependency — yields an async session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
