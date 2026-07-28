from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ChannelBase(BaseModel):
    channel_id: str
    channel_title: str | None = None
    channel_username: str | None = None
    feishu_chat_id: str = ""
    enabled: bool = True


class ChannelCreate(ChannelBase):
    pass


class ChannelUpdate(BaseModel):
    channel_title: str | None = None
    channel_username: str | None = None
    feishu_chat_id: str | None = None
    enabled: bool | None = None


class ChannelOut(ChannelBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    last_forwarded_id: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
