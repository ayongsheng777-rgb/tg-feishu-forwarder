"""DB model for Feishu chat aliases (group name <-> chat_id bindings).

Used by the Feishu command service to resolve human-friendly names
(e.g. "科技", "IT穿梭机") to Feishu chat IDs, so commands like
``tg采集 科技`` can locate the channels bound to that group.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, func

from app.core.database import Base


class ChatAlias(Base):
    __tablename__ = "chat_aliases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, index=True, comment="Alias / group display name")
    feishu_chat_id = Column(String(64), unique=True, nullable=False, comment="Feishu chat ID (oc_...)")
    auto_discovered = Column(Boolean, default=False, comment="True if auto-registered from an incoming command")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
