"""Rule engine — evaluates whether a TG message should be forwarded.

Rule types:
  - keyword_include  : forward only if message contains the keyword(s)
  - keyword_exclude  : skip if message contains the keyword(s)
  - regex            : forward only if message matches the regex
  - msg_type         : forward only if message type matches (text/photo/video/document)
"""
import re
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.tl import types

from app.models import Rule, Channel

logger = logging.getLogger(__name__)


class RuleEngine:
    """Evaluate forwarding rules for a given message."""

    async def evaluate(
        self,
        db: AsyncSession,
        channel_db_id: int | None,
        message_text: str,
        media_type: str,
    ) -> tuple[bool, str]:
        """Return (should_forward, reason).

        Rules are AND-combined: ALL include/regex/type rules must pass,
        and NONE of the exclude rules may match.
        """
        # Fetch all enabled rules — global (channel_id IS NULL) + channel-specific
        stmt = select(Rule).where(Rule.enabled == True)
        if channel_db_id is not None:
            stmt = stmt.where(
                (Rule.channel_id == None) | (Rule.channel_id == channel_db_id)
            )
        else:
            stmt = stmt.where(Rule.channel_id == None)

        result = await db.execute(stmt)
        rules = result.scalars().all()

        if not rules:
            return True, "no_rules"

        for rule in rules:
            ok, reason = self._check_single(rule, message_text, media_type)
            if not ok:
                return False, f"rule:{rule.name}({rule.rule_type}) — {reason}"

        return True, "all_rules_passed"

    def _check_single(self, rule: Rule, text: str, media_type: str) -> tuple[bool, str]:
        rt = rule.rule_type
        pat = rule.pattern or ""

        if rt == "keyword_include":
            keywords = [k.strip() for k in pat.split(",") if k.strip()]
            if not keywords:
                return True, "empty_pattern"
            matched = any(k.lower() in (text or "").lower() for k in keywords)
            return (matched, "matched" if matched else "not_found")

        elif rt == "keyword_exclude":
            keywords = [k.strip() for k in pat.split(",") if k.strip()]
            if not keywords:
                return True, "empty_pattern"
            excluded = any(k.lower() in (text or "").lower() for k in keywords)
            return (not excluded, "excluded" if excluded else "clean")

        elif rt == "regex":
            if not pat:
                return True, "empty_pattern"
            try:
                matched = bool(re.search(pat, text or ""))
                return (matched, "matched" if matched else "no_match")
            except re.error as e:
                logger.warning("Invalid regex in rule '%s': %s", rule.name, e)
                return True, "invalid_regex_skipped"

        elif rt == "msg_type":
            target = (rule.msg_type_value or pat or "all").strip().lower()
            if target == "all":
                return True, "type_all"
            return (media_type == target, f"type={media_type} vs {target}")

        # Unknown rule type — pass through
        return True, "unknown_type"

    async def get_media_type(self, event) -> str:
        """Determine the media type from a Telethon message event."""
        return self._get_media_type_from_msg(event.message)

    @staticmethod
    def _get_media_type_from_msg(msg) -> str:
        """Determine media type from a raw Telethon Message object.

        ⚠️ 不要用 ``msg.photo`` 判断「这条消息是图片」。Telethon 1.37 的
        ``Message.photo`` 在**消息本身没有媒体、但链接预览带图**时也会返回 truthy
        （源码里的 ``else`` 分支会取 ``web_preview.photo``）。这类消息本质是
        「文字 + 链接」，例如 ``https://fixupx.com/...``：

        - 它被判成 ``photo``，于是走 ``download_media`` + ``upload_image``；
        - 而 ``download_media`` 对 ``MessageMediaWebPage`` **优先取 document**，
          拿到的往往是预览里的 mp4；
        - 结果把 mp4 当图片传给飞书 → 234011「Can't recognize image format」
          或 234006「file size exceed」；
        - 又因为转发失败不推进 ``last_forwarded_id``，这一条会把**整个频道的
          后续消息永久卡死**（补抓是从该水位线往后拉的）。

        所以这里只认消息**自身**的媒体；链接预览只在「预览里确实是图片」时
        才沿用旧行为按图片转发（`download_media` 对 WebPage 优先取 document，
        所以必须确认没有 document 或 document 本身是图片）。
        """
        media = msg.media

        # 真正的图片
        if isinstance(media, types.MessageMediaPhoto):
            return "photo"

        # 真正的文档 / 视频 / 语音 / 贴纸
        if isinstance(media, types.MessageMediaDocument):
            doc = media.document
            mime = (getattr(doc, "mime_type", "") or "").lower()
            if msg.video:                      # DocumentAttributeVideo
                return "video"
            for attr in (getattr(doc, "attributes", None) or []):
                if isinstance(attr, types.DocumentAttributeAudio) and getattr(attr, "voice", False):
                    return "voice"
                if isinstance(attr, types.DocumentAttributeSticker):
                    return "sticker"
            if "gif" in mime:
                return "video"
            if "sticker" in mime:
                return "sticker"
            return "document"

        # 链接预览：只有「预览里是图片」才按 photo（保持既有行为，这类消息
        # 一直转发的是预览图且工作正常）；预览里是视频/文档则必须按文字，
        # 否则会下到 mp4 当图片上传 → 234011/234006。
        if isinstance(media, types.MessageMediaWebPage):
            wp = media.webpage
            if isinstance(wp, types.WebPage):
                doc = getattr(wp, "document", None)
                if doc is not None:
                    mime = (getattr(doc, "mime_type", "") or "").lower()
                    return "photo" if mime.startswith("image/") else "text"
                if isinstance(getattr(wp, "photo", None), types.Photo):
                    return "photo"
            return "text"

        # 其余（纯文字等）
        return "text"


rule_engine = RuleEngine()
