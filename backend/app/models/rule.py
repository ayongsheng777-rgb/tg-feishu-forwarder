"""DB models for forwarding rules (keyword / regex / message-type filters)."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, func, ForeignKey

from app.core.database import Base


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey("channels.id", ondelete="CASCADE"), nullable=True, comment="NULL = apply to all channels")
    name = Column(String(128), nullable=False, comment="Rule display name")
    rule_type = Column(String(32), nullable=False, comment="keyword_include | keyword_exclude | regex | msg_type")
    pattern = Column(Text, nullable=False, comment="Keyword / regex pattern / msg-type value")
    msg_type_value = Column(String(64), nullable=True, comment="text | photo | video | document | all")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
