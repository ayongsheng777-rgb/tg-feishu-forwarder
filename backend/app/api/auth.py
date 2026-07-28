"""Authentication API — login, OTP 2FA, first-run setup, account management."""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_temp_token, decode_token,
    generate_otp_secret, get_otp_uri, generate_qr_data_uri, verify_otp,
)
from app.models.user import User
from app.schemas.auth import (
    LoginRequest, OtpVerifyRequest, SetupRequest,
    EnableOtpRequest, DisableOtpRequest, ChangePasswordRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "otp_enabled": user.otp_enabled,
        "is_active": user.is_active,
    }


def _make_token(user: User) -> str:
    return create_access_token({"sub": str(user.id), "username": user.username})


# --------------------------------------------------------------------------- #
#  Status — no auth required
# --------------------------------------------------------------------------- #
@router.get("/status")
async def auth_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Check whether the app needs initial setup and whether the caller is logged in."""
    count = await db.scalar(select(func.count(User.id)))
    needs_setup = (count or 0) == 0

    user = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = decode_token(auth[7:])
            if payload.get("type") == "access":
                uid = int(payload.get("sub", 0))
                result = await db.execute(select(User).where(User.id == uid))
                user = result.scalar_one_or_none()
        except Exception:
            pass  # invalid / expired token — treat as not logged in

    return {
        "needs_setup": needs_setup,
        "authenticated": user is not None and user.is_active,
        "user": _user_to_dict(user) if user else None,
    }


# --------------------------------------------------------------------------- #
#  First-run admin setup
# --------------------------------------------------------------------------- #
@router.post("/setup")
async def setup(req: SetupRequest, db: AsyncSession = Depends(get_db)):
    """Create the initial admin account. Only works when zero users exist."""
    count = await db.scalar(select(func.count(User.id)))
    if count and count > 0:
        return {"error": "Setup already completed", "ok": False}

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("Initial admin account created: %s", user.username)
    token = _make_token(user)
    return {"ok": True, "token": token, "user": _user_to_dict(user)}


# --------------------------------------------------------------------------- #
#  Login + OTP
# --------------------------------------------------------------------------- #
@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Verify password. If 2FA is enabled, return a temp token; otherwise a full JWT."""
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        return {"error": "用户名或密码错误", "ok": False}
    if not user.is_active:
        return {"error": "账户已禁用", "ok": False}

    if user.otp_enabled and user.otp_secret:
        temp = create_temp_token({"sub": str(user.id), "username": user.username})
        return {"ok": True, "requires_otp": True, "temp_token": temp}

    token = _make_token(user)
    return {"ok": True, "token": token, "user": _user_to_dict(user)}


@router.post("/otp-verify")
async def otp_verify(req: OtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    """Verify the 6-digit OTP code using the temp token from the login step."""
    try:
        payload = decode_token(req.temp_token)
    except Exception:
        return {"error": "临时令牌已过期，请重新登录", "ok": False}

    if payload.get("type") != "temp":
        return {"error": "无效的令牌类型", "ok": False}

    uid = int(payload.get("sub", 0))
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.otp_secret:
        return {"error": "用户不存在或未配置 OTP", "ok": False}

    if not verify_otp(user.otp_secret, req.code):
        return {"error": "验证码不正确", "ok": False}

    token = _make_token(user)
    return {"ok": True, "token": token, "user": _user_to_dict(user)}


# --------------------------------------------------------------------------- #
#  Current user info — requires auth (user_id injected by middleware)
# --------------------------------------------------------------------------- #
@router.get("/me")
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return {"error": "Not authenticated", "ok": False}
    result = await db.execute(select(User).where(User.id == int(uid)))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found", "ok": False}
    return {"ok": True, "user": _user_to_dict(user)}


# --------------------------------------------------------------------------- #
#  2FA setup & management — requires auth
# --------------------------------------------------------------------------- #
@router.post("/setup-otp")
async def setup_otp(request: Request, db: AsyncSession = Depends(get_db)):
    """Generate a new TOTP secret and return QR code + manual key."""
    uid = int(getattr(request.state, "user_id", 0) or 0)
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found", "ok": False}

    secret = generate_otp_secret()
    user.otp_secret = secret
    await db.commit()

    uri = get_otp_uri(secret, user.username)
    qr = generate_qr_data_uri(uri)
    return {"ok": True, "secret": secret, "qr_code": qr, "uri": uri}


@router.post("/enable-otp")
async def enable_otp(req: EnableOtpRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Verify a code to confirm the OTP setup and enable 2FA."""
    uid = int(getattr(request.state, "user_id", 0) or 0)
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.otp_secret:
        return {"error": "请先调用 setup-otp 生成密钥", "ok": False}

    if not verify_otp(user.otp_secret, req.code):
        return {"error": "验证码不正确，请重试", "ok": False}

    user.otp_enabled = True
    await db.commit()
    logger.info("2FA enabled for user %s", user.username)
    return {"ok": True, "message": "两步验证已启用"}


@router.post("/disable-otp")
async def disable_otp(req: DisableOtpRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Disable 2FA — requires current password as confirmation."""
    uid = int(getattr(request.state, "user_id", 0) or 0)
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found", "ok": False}

    if not verify_password(req.password, user.password_hash):
        return {"error": "密码不正确", "ok": False}

    user.otp_enabled = False
    user.otp_secret = None
    await db.commit()
    logger.info("2FA disabled for user %s", user.username)
    return {"ok": True, "message": "两步验证已关闭"}


# --------------------------------------------------------------------------- #
#  Change password
# --------------------------------------------------------------------------- #
@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    uid = int(getattr(request.state, "user_id", 0) or 0)
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found", "ok": False}

    if not verify_password(req.old_password, user.password_hash):
        return {"error": "原密码不正确", "ok": False}

    user.password_hash = hash_password(req.new_password)
    await db.commit()
    logger.info("Password changed for user %s", user.username)
    return {"ok": True, "message": "密码已修改"}


# --------------------------------------------------------------------------- #
#  Logout — stateless JWT, client just discards the token
# --------------------------------------------------------------------------- #
@router.post("/logout")
async def logout():
    return {"ok": True, "message": "已退出登录"}
