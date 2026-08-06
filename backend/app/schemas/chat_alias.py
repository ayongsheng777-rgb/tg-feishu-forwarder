from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ChatAliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    feishu_chat_id: str
    auto_discovered: bool = False
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatAliasCreate(BaseModel):
    name: str
    feishu_chat_id: str
    enabled: bool = True


class ChatAliasUpdate(BaseModel):
    name: str | None = None
    feishu_chat_id: str | None = None
    enabled: bool | None = None
