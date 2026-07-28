"""DB model for forwarding logs (audit trail)."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, func, ForeignKey

from app.core.database import Base


class ForwardLog(Base):
    __tablename__ = "forward_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    tg_message_id = Column(Integer, nullable=True)
    tg_sender = Column(String(256), nullable=True)
    message_text = Column(Text, nullable=True)
    feishu_message_id = Column(String(64), nullable=True)
    status = Column(String(32), default="pending", comment="pending | success | failed | skipped")
    error_message = Column(Text, nullable=True)
    media_type = Column(String(32), nullable=True, comment="text | photo | video | document | sticker | none")
    created_at = Column(DateTime, server_default=func.now())
