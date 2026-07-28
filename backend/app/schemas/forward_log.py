from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ForwardLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    channel_id: int | None = None
    tg_message_id: int | None = None
    tg_sender: str | None = None
    message_text: str | None = None
    feishu_message_id: str | None = None
    status: str = "pending"
    error_message: str | None = None
    media_type: str | None = None
    created_at: datetime | None = None
