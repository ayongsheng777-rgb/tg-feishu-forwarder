from datetime import datetime
from pydantic import BaseModel, ConfigDict


class RuleBase(BaseModel):
    channel_id: int | None = None
    name: str
    rule_type: str  # keyword_include | keyword_exclude | regex | msg_type
    pattern: str
    msg_type_value: str | None = None
    enabled: bool = True


class RuleCreate(RuleBase):
    pass


class RuleUpdate(BaseModel):
    channel_id: int | None = None
    name: str | None = None
    rule_type: str | None = None
    pattern: str | None = None
    msg_type_value: str | None = None
    enabled: bool | None = None


class RuleOut(RuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
