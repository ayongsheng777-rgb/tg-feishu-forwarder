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


class ImageNotSupportedError(Exception):
    """本地无法把图片转成飞书可接受的格式。

    典型场景：文件已损坏、下载不完整（0 字节）、根本不是图片，
    或缺少对应解码器。这种情况重试没有意义，必须上抛让调用方决定
    （转发器会降级为「文字 + 原文链接」）。
    """


def _head_hex(path: str, n: int = 12) -> str:
    """读文件头若干字节的十六进制，用于判断真实格式（排障用）。"""
    try:
        with open(path, "rb") as f:
            return f.read(n).hex()
    except Exception:
        return "?"



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

        飞书 ``/im/v1/images``（``image_type=message``）只接受
        jpg/jpeg/png/webp/gif/bmp，且 ≤ 10 MB、长边 ≤ 1500 px。
        这里先把图片**归一化成合规文件**（必要时重编码为 JPEG）再上传；
        若飞书仍以 234011/234006 拒绝，则强制转 JPEG 再试一次。

        本地根本无法解码时抛 :class:`ImageNotSupportedError` —— 重试无意义。
        """
        await self._ensure_token()
        try:
            return await self._upload_image_once(file_path, force_jpeg=False)
        except ImageNotSupportedError:
            raise
        except RuntimeError as exc:
            logger.warning("[img] 飞书拒绝原图，改用 JPEG 重试: %s — %s", file_path, exc)
            return await self._upload_image_once(file_path, force_jpeg=True)

    async def _upload_image_once(self, file_path: str, force_jpeg: bool) -> str:
        import os as _os

        original_name = _os.path.basename(file_path)
        processed_path, upload_name, content_type = self._prepare_image(
            file_path, original_name, force=force_jpeg
        )
        try:
            with open(processed_path, "rb") as f:
                file_bytes = f.read()
            logger.info(
                "[img] 上传 %s → %s (%s, %.2f MB, %s)",
                original_name, upload_name, content_type, len(file_bytes) / 1048576,
                "JPEG 重编码" if processed_path != file_path else "原图直传",
            )
            resp = await self._http.post(
                f"{FEISHU_BASE}/im/v1/images",
                data={"image_type": "message"},
                headers={"Authorization": f"Bearer {self._token}"},
                files={"image": (upload_name, file_bytes, content_type)},
            )
        finally:
            if processed_path != file_path and _os.path.exists(processed_path):
                _os.remove(processed_path)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Image upload failed: {data}")
        return data["data"]["image_key"]

    # 飞书 /im/v1/images 的硬限制
    IMAGE_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
    IMAGE_MAX_DIM = 1500                 # 长边像素
    # 可原样直传的格式（PIL 的 format 名）；其余一律转 JPEG
    _PASSTHROUGH_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
    _CT_BY_FORMAT = {
        "JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp",
        "GIF": "image/gif", "BMP": "image/bmp",
    }

    @classmethod
    def _prepare_image(cls, file_path: str, original_name: str, force: bool = False):
        """把图片归一化成飞书可接受的上传件。

        返回 ``(本地路径, 上传文件名, content_type)``；无需处理时原样返回。

        ``force=True`` 时无论原格式如何都重编码为 JPEG（用于飞书报
        234011「Can't recognize image format」后的重试）。

        ⚠️ 旧实现有两个坑，是 4 位数图片持续转发失败的根因：
        1. ``Image.open`` 失败时**原样返回**，调用方却仍把文件名改成 ``.jpg``、
           content_type 写成 ``image/jpeg`` —— 于是把非 JPEG 的字节谎报成 JPEG，
           飞书必然回 234011；
        2. 超限时只降 quality、**从不降分辨率**，超大图可能压不到 10 MB 以下，
           于是回 234006。
        现在改为：解码失败直接抛错（不再谎报格式），压缩时先降 quality、
        再按 0.75 逐步降分辨率，直到真的小于 10 MB。
        """
        import os as _os
        import tempfile
        from PIL import Image, ImageFile

        ImageFile.LOAD_TRUNCATED_IMAGES = True
        size_bytes = _os.path.getsize(file_path)

        try:
            img = Image.open(file_path)
            fmt = (img.format or "").upper()
            # 超大图先降低解码尺寸（仅 JPEG 的 draft 生效，其它格式自动忽略），
            # 避免一次性解码出几百 MB 的位图
            if max(img.size) > cls.IMAGE_MAX_DIM * 2:
                try:
                    img.draft("RGB", (cls.IMAGE_MAX_DIM, cls.IMAGE_MAX_DIM))
                except Exception:
                    pass
            img.load()   # 真正解码；损坏 / 截断的文件在这里暴露
        except Exception as exc:
            raise ImageNotSupportedError(
                f"图片无法解码（{original_name}, {size_bytes} bytes, "
                f"头部 {_head_hex(file_path)}）: {type(exc).__name__}: {exc}"
            ) from exc

        w, h = img.size
        needs_resize = max(w, h) > cls.IMAGE_MAX_DIM
        needs_shrink = size_bytes > cls.IMAGE_MAX_BYTES
        needs_convert = force or fmt not in cls._PASSTHROUGH_FORMATS

        if not (needs_resize or needs_shrink or needs_convert):
            return file_path, original_name, cls._CT_BY_FORMAT.get(fmt, "image/jpeg")

        # ---- 重编码为 JPEG ----
        if needs_resize:
            ratio = cls.IMAGE_MAX_DIM / max(w, h)
            img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)

        # 去透明 / 统一到 RGB（JPEG 不支持 alpha）
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        _os.makedirs(settings.media_cache_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", dir=settings.media_cache_dir)
        _os.close(fd)
        try:
            quality = 85
            for _ in range(14):
                img.save(tmp_path, "JPEG", quality=quality, optimize=True, progressive=True)
                if _os.path.getsize(tmp_path) <= cls.IMAGE_MAX_BYTES:
                    break
                if quality > 45:
                    quality -= 12          # 先降质量
                else:
                    w2, h2 = img.size      # 质量降到底仍超限 → 降分辨率
                    if max(w2, h2) <= 320:
                        break
                    img = img.resize(
                        (max(1, int(w2 * 0.75)), max(1, int(h2 * 0.75))), Image.LANCZOS
                    )
                    quality = 72
            if _os.path.getsize(tmp_path) > cls.IMAGE_MAX_BYTES:
                raise ImageNotSupportedError(
                    f"图片压缩后仍超过 {cls.IMAGE_MAX_BYTES // 1048576}MB（{original_name}）"
                )
        except Exception:
            if _os.path.exists(tmp_path):
                _os.remove(tmp_path)
            raise

        upload_name = _os.path.splitext(original_name)[0] + ".jpg"
        logger.info(
            "[img] 归一化 %s (%s %dx%d, %.2f MB) → %s (%.2f MB)",
            original_name, fmt or "?", w, h, size_bytes / 1048576,
            upload_name, _os.path.getsize(tmp_path) / 1048576,
        )
        return tmp_path, upload_name, "image/jpeg"

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
