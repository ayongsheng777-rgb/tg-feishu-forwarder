from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str
    value: str | None = None
    updated_at: datetime | None = None


class SettingUpdate(BaseModel):
    value: str
