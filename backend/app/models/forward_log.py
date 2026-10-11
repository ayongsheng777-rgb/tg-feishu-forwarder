"""DB model for forwarding logs (audit trail)."""
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Text,
    func,
    ForeignKey,
    Index,
)

from app.core.database import Base


class ForwardLog(Base):
    __tablename__ = "forward_logs"

    # 索引 —— 日志表和仪表盘都是「按 时间 / 状态 / 频道」筛选聚合。
    # 之前一个索引都没有，5 万行以上时每次 COUNT 都是全表扫描，
    # 仪表盘接口实测要 30s（详见 2026-10-10 的排障记录）。
    __table_args__ = (
        # 覆盖索引：仪表盘那条 GROUP BY channel_id, status 的聚合可直接走它
        Index("ix_forward_logs_channel_status_created", "channel_id", "status", "created_at"),
        # 日志列表按 id 倒序翻页、按时间过滤
        Index("ix_forward_logs_created_at", "created_at"),
        # 按状态筛选（失败重试列表、状态统计）
        Index("ix_forward_logs_status", "status"),
    )

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
