"""Security utilities: password hashing, JWT tokens, OTP (TOTP 2FA)."""
import base64
import io
import logging
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
from jose import jwt, JWTError
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# --- Password hashing ---
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- JWT ---
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(hours=24)
TEMP_TOKEN_EXPIRE = timedelta(minutes=3)

# Module-level secret — set at startup from DB
_jwt_secret: str | None = None


def set_jwt_secret(secret: str):
    global _jwt_secret
    _jwt_secret = secret


def get_jwt_secret() -> str:
    if not _jwt_secret:
        raise RuntimeError("JWT secret not initialized — call ensure_jwt_secret() at startup")
    return _jwt_secret


async def ensure_jwt_secret():
    """Load JWT secret from DB settings; generate one if it doesn't exist."""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models import Setting

    async with async_session() as db:
        result = await db.execute(select(Setting).where(Setting.key == "jwt_secret"))
        row = result.scalar_one_or_none()
        if row and row.value:
            set_jwt_secret(row.value)
            logger.info("JWT secret loaded from DB")
            return

        secret = secrets.token_urlsafe(48)
        row = Setting(key="jwt_secret", value=secret)
        db.add(row)
        await db.commit()
        set_jwt_secret(secret)
        logger.info("Generated new JWT secret and stored in DB")


# --- Password ---
def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# --- JWT tokens ---
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or ACCESS_TOKEN_EXPIRE)
    to_encode["exp"] = expire
    to_encode["type"] = "access"
    return jwt.encode(to_encode, get_jwt_secret(), algorithm=ALGORITHM)


def create_temp_token(data: dict) -> str:
    """Short-lived token used between password verification and OTP step."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + TEMP_TOKEN_EXPIRE
    to_encode["exp"] = expire
    to_encode["type"] = "temp"
    return jwt.encode(to_encode, get_jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT. Raises JWTError on failure."""
    return jwt.decode(token, get_jwt_secret(), algorithms=[ALGORITHM])


# --- OTP (TOTP) ---
def generate_otp_secret() -> str:
    return pyotp.random_base32()


def get_otp_uri(secret: str, username: str, issuer: str = "TG-Feishu Forwarder") -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def generate_qr_data_uri(data: str) -> str:
    """Generate a base64 data URI for a QR code encoding *data*."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def verify_otp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code. Allows ±1 window (~60 s) leeway."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)
