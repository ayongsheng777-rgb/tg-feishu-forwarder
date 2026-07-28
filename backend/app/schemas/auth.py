"""Pydantic schemas for auth requests & responses."""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class OtpVerifyRequest(BaseModel):
    temp_token: str
    code: str = Field(..., min_length=6, max_length=6)


class SetupRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


class EnableOtpRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class DisableOtpRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    otp_enabled: bool
    is_active: bool


class LoginResponse(BaseModel):
    token: str | None = None
    requires_otp: bool = False
    temp_token: str | None = None
    user: dict | None = None
