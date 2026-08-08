"""全量 TG 指令链路测试（临时 DB + mock 外部依赖，零副作用）。

运行方式（在 backend 容器内）：
    python tools/tg_full_chain_test.py

特点：
- 使用独立临时 sqlite（/tmp/tgfeishu_test.db），不触碰真实数据
- mock 飞书发送 / TG 登录·加入 / 监听器启停 / 采集 / 状态上报
- 覆盖全部 16 组指令 × 大小写前缀变体 × 代表性参数
- 检测真实异常（handler 内未捕获崩溃会以「失败:」回执暴露，被标记 FAIL）
"""
import os
import sys
import asyncio

DB_PATH = "/tmp/tgfeishu_test.db"
os.environ["DB_URL"] = f"sqlite+aiosqlite:////{DB_PATH}"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

from unittest.mock import AsyncMock, MagicMock

import app.models  # noqa: F401  (注册全部表)
from app.core.database import Base, engine, async_session, init_db  # noqa: E402
from app.models import Channel, ChatAlias, ForwardLog, Setting  # noqa: E402


async def seed():
    async with async_session() as db:
        db.add(Channel(channel_id=999001, channel_title="测试频道A", channel_username="testa", feishu_chat_id="oc_test", enabled=True))
        db.add(Channel(channel_id=999002, channel_title="测试频道B", channel_username="testb", feishu_chat_id="oc_test", enabled=True))
        db.add(ChatAlias(name="测试群", feishu_chat_id="oc_test", auto_discovered=False, enabled=True))
        db.add(ForwardLog(channel_id=999001, status="success", message_text="ok"))
        db.add(ForwardLog(channel_id=999001, status="failed", message_text="bad"))
        db.add(Setting(key="catchup_interval_minutes", value="2"))
        await db.commit()


# ---- mock 外部边界 ----
import importlib

fc_mod = importlib.import_module("app.services.feishu_client")
tc_mod = importlib.import_module("app.services.telegram_client")
tg_api = importlib.import_module("app.api.telegram")
fwd_mod = importlib.import_module("app.services.forwarder")
sr_mod = importlib.import_module("app.services.status_reporter")
feishu_command_mod = importlib.import_module("app.services.feishu_command")

sent = []


async def fake_send(chat_id, text):
    sent.append((chat_id, text))


fc_mod.feishu_client.send_text = fake_send
fc_mod.feishu_client.get_chat_name = AsyncMock(return_value="测试群")

tg_mgr = MagicMock()
tg_mgr._listener_task = None
tg_mgr.join_channel = AsyncMock(return_value={"ok": True, "channel_id": 999003, "title": "新频道", "username": "newc"})
tg_mgr.start_login = AsyncMock()
tg_mgr.complete_login = AsyncMock(return_value={"success": True})
tg_mgr.stop_listener = AsyncMock()
tc_mod.tg_manager = tg_mgr

tg_api.start_listener_impl = AsyncMock(return_value=[999001, 999002])
tg_api._set_listener_pref = AsyncMock()
tg_api.reload_listener_impl = AsyncMock()

fwd_mod.forwarder.manual_fetch = AsyncMock(return_value={"success": True, "fetched": 2, "forwarded": 2, "skipped": 0, "failed": 0})
fwd_mod.forwarder.fetch_by_date = AsyncMock(return_value={"success": True, "fetched": 1, "forwarded": 1, "skipped": 0, "failed": 0})

sr_mod.send_report = AsyncMock(return_value={"success": True})

from app.services.feishu_command import FeishuCommandService  # noqa: E402

svc = feishu_command_mod.FeishuCommandService()
restart_called = []


async def fake_restart(chat_id):
    restart_called.append(chat_id)


svc._handle_restart = fake_restart  # 避免真实 os._exit


# ---- 用例 ----
# (指令, 预期: 'reply'=必须有回执, 'noreply'=静默忽略, 'restart'=记录重启)
CASES = [
    ("tg帮助", "reply"),
    ("TG帮助", "reply"), ("Tg帮助", "reply"), ("tG帮助", "reply"), ("TG 帮助", "reply"),
    ("tg采集", "reply"),
    ("tg采集 测试频道", "reply"),
    ("tg采集 测试群", "reply"),
    ("tg采集 2026-01-01", "reply"),
    ("tg采集 2026-01-01 2026-01-02", "reply"),
    ("tg暂停 测试频道A", "reply"),
    ("tg恢复 测试频道A", "reply"),
    ("tg移除 测试频道B", "reply"),
    ("tg监听", "reply"), ("tg监控", "reply"),
    ("TG监控 开启", "reply"), ("TG监听 关闭", "reply"),
    ("tg重启", "restart"),
    ("tg登录 +8613800138000", "reply"),
    ("tg验证码 123456", "reply"),
    ("tg密码 mypass", "reply"),
    ("tg补抓", "reply"), ("tg补抓 5", "reply"), ("tg补抓 0", "reply"),
    ("tg订阅 新频道 @newc 测试群", "reply"),
    ("tg清理 日志", "reply"), ("tg清理 日志 确认", "reply"),
    ("tg绑定 新群 oc_new123", "reply"),
    ("tg群列表", "reply"),
    ("tg状态", "reply"), ("tg状态 测试群", "reply"),
    ("乱码指令xyz", "noreply"),
]

FAIL_MARKERS = ("失败:", "Traceback", "Exception:")


async def main():
    await init_db()
    await seed()

    passed = 0
    failed = 0
    for cmd, expect in CASES:
        sent.clear()
        restart_called.clear()
        try:
            await svc._process_command("oc_test", cmd)
        except Exception as e:  # 未捕获异常 = 真 bug
            print(f"FAIL  {cmd!r}: 未捕获异常 {type(e).__name__}: {e}")
            failed += 1
            continue

        if expect == "restart":
            ok = len(restart_called) == 1
            print(f"{'PASS' if ok else 'FAIL'}  {cmd!r}: 重启指令{'已触发' if ok else '未触发'}")
            passed += ok
            failed += (not ok)
            continue

        if expect == "noreply":
            ok = len(sent) == 0
            print(f"{'PASS' if ok else 'FAIL'}  {cmd!r}: 未知指令{'已静默忽略' if ok else '竟有回执'}")
            passed += ok
            failed += (not ok)
            continue

        # reply
        if not sent:
            print(f"FAIL  {cmd!r}: 无回执")
            failed += 1
            continue
        text = sent[0][1]
        crashed = any(m in text for m in FAIL_MARKERS)
        ok = not crashed
        print(f"{'PASS' if ok else 'FAIL'}  {cmd!r}: {text[:48]!r}")
        passed += ok
        failed += (not ok)

    print(f"\n==== 全量 TG 指令测试: {passed} PASS / {failed} FAIL / 共 {len(CASES)} ====")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
