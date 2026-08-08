"""Feishu (Lark) Open Platform API client.

Handles:
  - Tenant access token management (auto-refresh)
  - Sending text / rich-text / card messages
  - Uploading media (images, videos, files) to Feishu IM
"""
import json
import time
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuNotInChatError(Exception):
    """Raised when the Feishu bot is not a member of the target chat.

    Feishu API returns code 230002 ("Bot/User can NOT be out of the chat")
    for this.  It is a *permanent* condition (the bot must be invited into
    the group by a member) and must NOT be retried endlessly.
    """



class FeishuClient:
    """Feishu API wrapper with automatic token refresh."""

    def __init__(self, app_id: str = "", app_secret: str = ""):
        self.app_id = app_id or settings.feishu_app_id
        self.app_secret = app_secret or settings.feishu_app_secret
        self._token: str = ""
        self._token_expires: float = 0.0
        self._http = httpx.AsyncClient(timeout=120)

    async def close(self):
        await self._http.aclose()

    async def _load_credentials_from_db(self):
        """Try to load Feishu credentials from the DB settings table."""
        try:
            from app.core.database import async_session
            from app.models import Setting
            from sqlalchemy import select
            async with async_session() as session:
                result = await session.execute(
                    select(Setting).where(
                        Setting.key.in_(["feishu_app_id", "feishu_app_secret"])
                    )
                )
                for s in result.scalars().all():
                    if s.key == "feishu_app_id" and s.value:
                        self.app_id = s.value
                    elif s.key == "feishu_app_secret" and s.value:
                        self.app_secret = s.value
        except Exception as e:
            logger.debug("Could not load Feishu credentials from DB: %s", e)

    def update_credentials(self, app_id: str, app_secret: str):
        """Update credentials at runtime (called from Settings API)."""
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = ""
        self._token_expires = 0.0

    # ------------------------------------------------------------------
    #  Token management
    # ------------------------------------------------------------------

    async def _ensure_token(self):
        """Refresh the tenant access token if expired."""
        if self._token and time.time() < self._token_expires - 60:
            return
        # Dynamically load credentials from DB settings if env vars are empty
        if not self.app_id or not self.app_secret:
            await self._load_credentials_from_db()
        if not self.app_id or not self.app_secret:
            raise ValueError("Feishu app_id / app_secret not configured")
        resp = await self._http.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get tenant token: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    #  Messaging
    # ------------------------------------------------------------------

    @staticmethod
    def _check_resp(data: dict, action: str = "Send message") -> dict:
        """Raise on Feishu API-level errors (HTTP 200/400 both possible).

        Without this, failures like code 230002 (bot not in chat) were
        silently treated as successful sends.  Code 230002 is raised as the
        distinct :class:`FeishuNotInChatError` so callers can skip (not retry)
        the message instead of failing forever.
        """
        if data.get("code") != 0:
            code = data.get("code")
            if code == 230002:
                raise FeishuNotInChatError(
                    f"{action} failed: bot not in chat (code=230002)"
                )
            raise RuntimeError(f"{action} failed: {data.get('msg')} (code={code})")
        return data

    async def send_text(self, chat_id: str, text: str) -> dict:
        """Send a plain text message."""
        await self._ensure_token()
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": f'{{"text": {_json_str(text)}}}',
            },
        )
        return self._check_resp(resp.json(), "Send text")

    async def send_card(self, chat_id: str, card: dict) -> dict:
        """Send an interactive card message."""
        await self._ensure_token()
        import json
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
        )
        return self._check_resp(resp.json(), "Send card")

    async def send_rich_text(self, chat_id: str, title: str, elements: list[dict]) -> dict:
        """Send a rich-text post message.

        ``elements`` is a list of rich-text segment dicts, e.g.:
            [{"tag": "text", "text": "Hello "},
             {"tag": "a", "text": "link", "href": "https://..."}]
        """
        await self._ensure_token()
        import json
        post_content = {
            "zh_cn": {
                "title": title,
                "content": [elements],
            }
        }
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "post",
                "content": json.dumps(post_content, ensure_ascii=False),
            },
        )
        return self._check_resp(resp.json(), "Send rich text")

    async def send_image(self, chat_id: str, image_key: str) -> dict:
        """Send an image message using a pre-uploaded image_key."""
        await self._ensure_token()
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "image",
                "content": f'{{"image_key": "{image_key}"}}',
            },
        )
        return self._check_resp(resp.json(), "Send image")

    async def send_video(self, chat_id: str, video_key: str, file_key: str = "") -> dict:
        """Send a video message using a pre-uploaded file_key.

        Feishu has no ``video`` msg_type -- videos are sent as ``media``.
        The file MUST have been uploaded with ``file_type="mp4"``, otherwise
        the returned file_key is not accepted as playable media.
        ``video_key`` is the optional cover image_key.
        """
        await self._ensure_token()
        payload = {"file_key": file_key}
        if video_key:
            payload["image_key"] = video_key
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "media",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        )
        return self._check_resp(resp.json(), "Send video")

    async def send_file(self, chat_id: str, file_key: str) -> dict:
        """Send a file message using a pre-uploaded file_key."""
        await self._ensure_token()
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=self._headers(),
            json={
                "receive_id": chat_id,
                "msg_type": "file",
                "content": f'{{"file_key": "{file_key}"}}',
            },
        )
        return self._check_resp(resp.json(), "Send file")

    async def get_chat_name(self, chat_id: str) -> str:
        """Fetch a chat's display name via the Open API. Returns '' on failure."""
        await self._ensure_token()
        resp = await self._http.get(
            f"{FEISHU_BASE}/im/v1/chats/{chat_id}",
            headers=self._headers(),
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get chat info: {data}")
        return data.get("data", {}).get("name", "") or ""

    # ------------------------------------------------------------------
    #  Media upload
    # ------------------------------------------------------------------

    async def upload_image(self, file_path: str) -> str:
        """Upload an image file and return the image_key.

        Feishu's ``image_type=message`` requires images ≤ 1500×1500 px
        and ≤ 10 MB.  Oversized images are auto-resized/compressed via Pillow.
        If Feishu rejects the format (e.g. code 234011 "Can't recognize image
        format" — common with TG webp/sticker exports), the image is
        force-converted to JPEG and retried once.
        """
        await self._ensure_token()
        try:
            return await self._upload_image_once(file_path, force_jpeg=False)
        except RuntimeError:
            logger.info("Image upload rejected, retrying with forced JPEG: %s", file_path)
            return await self._upload_image_once(file_path, force_jpeg=True)

    async def _upload_image_once(self, file_path: str, force_jpeg: bool) -> str:
        import os as _os

        filename = _os.path.basename(file_path)
        processed_path = self._prepare_image(file_path, force=force_jpeg)
        try:
            with open(processed_path, "rb") as f:
                file_bytes = f.read()
            if force_jpeg or processed_path.endswith(".jpg"):
                filename = _os.path.splitext(filename)[0] + ".jpg"
                content_type = "image/jpeg"
            else:
                ext = _os.path.splitext(filename)[1].lower() or ".jpg"
                ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                          ".png": "image/png", ".webp": "image/webp",
                          ".gif": "image/gif", ".bmp": "image/bmp"}
                content_type = ct_map.get(ext, "image/jpeg")
            resp = await self._http.post(
                f"{FEISHU_BASE}/im/v1/images",
                data={"image_type": "message"},
                headers={"Authorization": f"Bearer {self._token}"},
                files={"image": (filename, file_bytes, content_type)},
            )
        finally:
            if processed_path != file_path and _os.path.exists(processed_path):
                _os.remove(processed_path)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Image upload failed: {data}")
        return data["data"]["image_key"]

    @staticmethod
    def _prepare_image(file_path: str, force: bool = False) -> str:
        """Resize / compress an image to meet Feishu limits.

        Returns the original path if no processing is needed, or a
        temporary path for the processed image.  With ``force=True`` the
        image is always converted to a clean JPEG (used as a retry when
        Feishu can't recognize the original format).
        """
        import os as _os
        import tempfile
        from PIL import Image

        try:
            img = Image.open(file_path)
        except Exception:
            # Not a valid PIL image — return as-is (let Feishu reject it)
            return file_path

        MAX_DIM = 1500
        MAX_BYTES = 10 * 1024 * 1024  # 10 MB

        w, h = img.size
        needs_resize = w > MAX_DIM or h > MAX_DIM
        needs_recompress = _os.path.getsize(file_path) > MAX_BYTES

        if not force and not needs_resize and not needs_recompress:
            return file_path

        # Resize if needed (preserve aspect ratio)
        if needs_resize:
            ratio = min(MAX_DIM / w, MAX_DIM / h)
            new_size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
            img = img.resize(new_size, Image.LANCZOS)

        # Convert to RGB if needed (e.g. RGBA, P mode)
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Save as JPEG with progressive compression
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", dir="/app/media_cache")
        _os.close(fd)
        quality = 85
        img.save(tmp_path, "JPEG", quality=quality, optimize=True)

        # If still too large, reduce quality iteratively
        while _os.path.getsize(tmp_path) > MAX_BYTES and quality > 20:
            quality -= 10
            img.save(tmp_path, "JPEG", quality=quality, optimize=True)

        return tmp_path

    async def upload_file(self, file_path: str, file_type: str = "stream") -> str:
        """Upload a file (video/document) and return the file_key.

        ``file_type`` should be ``mp4`` for video or ``stream`` for other files.
        """
        await self._ensure_token()
        import os
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        # Determine content type
        ext = os.path.splitext(filename)[1].lower()
        ct_map = {".mp4": "video/mp4", ".pdf": "application/pdf",
                  ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  ".zip": "application/zip", ".txt": "text/plain"}
        content_type = ct_map.get(ext, "application/octet-stream")
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/files",
            data={"file_type": file_type, "file_name": filename},
            headers={"Authorization": f"Bearer {self._token}"},
            files={"file": (filename, file_bytes, content_type)},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"File upload failed: {data}")
        return data["data"]["file_key"]


def _json_str(s: str) -> str:
    """Safely JSON-encode a string (adds quotes, escapes)."""
    import json
    return json.dumps(s, ensure_ascii=False)


# Module-level singleton (lazily uses settings)
feishu_client = FeishuClient()
