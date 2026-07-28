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
        """Determine media type from a raw Telethon Message object."""
        if msg.photo:
            return "photo"
        if msg.video:
            return "video"
        if msg.document:
            mime = getattr(msg.document, "mime_type", "") or ""
            if "gif" in mime:
                return "video"
            if "sticker" in mime:
                return "sticker"
            return "document"
        if msg.voice:
            return "voice"
        return "text"


rule_engine = RuleEngine()
