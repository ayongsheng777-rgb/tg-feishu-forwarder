"""全量频道转发健康巡检。只读。"""
import asyncio
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

from sqlalchemy import select, func

from app.core.database import async_session
from app.models.channel import Channel
from app.models.forward_log import ForwardLog


async def main() -> None:
    since = datetime.utcnow() - timedelta(minutes=30)
    async with async_session() as db:
        rows = (await db.execute(select(Channel))).scalars().all()
        print(f"{'频道':<34} {'启用':<5} {'游标':<9} 近30分钟状态")
        print("-" * 90)
        for ch in rows:
            recent = (
                await db.execute(
                    select(ForwardLog.status, func.count())
                    .where(ForwardLog.channel_id == ch.id)
                    .where(ForwardLog.created_at >= since)
                    .group_by(ForwardLog.status)
                )
            ).all()
            title = (ch.channel_title or str(ch.channel_id))[:32]
            print(
                f"{title:<34} {str(ch.enabled):<5} "
                f"{str(ch.last_forwarded_id):<9} "
                f"{ch.feishu_chat_id[:24]:<26} {dict(recent)}"
            )

        print()
        print("近30分钟失败样本（按错误码聚合）：")
        fails = (
            await db.execute(
                select(ForwardLog)
                .where(ForwardLog.created_at >= since)
                .where(ForwardLog.status.in_(["failed", "skipped"]))
                .order_by(ForwardLog.id.desc())
                .limit(15)
            )
        ).scalars().all()
        if not fails:
            print("  无")
        for lg in fails:
            print(
                f"  #{lg.id} ch={lg.channel_id} msg={lg.tg_message_id} "
                f"{lg.status} {(lg.error_message or '')[:80]}"
            )


asyncio.run(main())
