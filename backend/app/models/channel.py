"""DB models for monitored TG channels."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, func

from app.core.database import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(64), nullable=False, index=True, comment="TG channel/chat ID (may duplicate for multi-target forwarding)")
    channel_title = Column(String(256), nullable=True, comment="Display name")
    channel_username = Column(String(256), nullable=True, comment="@username if available")
    feishu_chat_id = Column(String(64), nullable=False, default="", comment="Target Feishu chat ID")
    enabled = Column(Boolean, default=True, comment="Whether forwarding is active")
    last_forwarded_id = Column(Integer, default=0, comment="Last forwarded message ID for dedup")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
