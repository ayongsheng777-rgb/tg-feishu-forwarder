# AGENTS.md — AI 智能体维护手册（青鸟 · TG→飞书消息转发系统）

> 写给接管本项目的 AI 智能体。这不是 README（README 给人快速上手），
> 这是实战交接：系统为什么这样设计、怎么安全地改、哪些坑已经踩过。
> 全部内容来自真实排障与修复记录（截至 2026-08-07，main @ 7cbf734）。

---

## 1. 项目一句话

**青鸟（QingNiao）**——把 Telegram 频道的图文/视频消息自动、选择性地转发到
飞书群组的系统。用户在飞书群里直接用 `tg` 前缀命令控制采集、绑定群、查状态，
后台可可视化配置频道、规则、群绑定。Docker Compose 部署（4 服务 + Redis），
GitHub: `ayongsheng777-rgb/tg-feishu-forwarder`（Private，main 直推）。

> 青鸟衔信，穿山越岭——TG 消息一站直达飞书。

## 2. 架构与运行事实

### 服务拓扑（Docker Compose，全部走 `restart: always`）

```
Telegram (Telethon 监听)               飞书群 (lark-oapi 命令)
        │                                    │
        ▼                                    ▼
   backend :8010  ←── FastAPI + Telethon 监听 + 命令分发
   (TG 登录 / 频道 / 转发 / 飞书命令 WebSocket)        │
        │                                   │ 发消息回飞书群
        ├→ Celery Worker (异步媒体下载/上传)           │
        │     │ 失败自动重试            feishu_client (Open API)
        │     ▼                                    │
        └→ Redis :6379 (队列，仅容器内部)            │
   frontend :8090  ←── Vue3 + Element Plus 管理后台 │
                                              feishu_command (WS 长连接收命令)
```

### 关键端口 / 凭证 / 路径

| 项 | 值 |
|---|---|
| 前端后台 | http://localhost:8090 |
| 后端 API (Swagger) | http://localhost:8010/docs |
| 后端端口(容器内) | 8000 → 宿主机 8010 |
| 前端端口(容器内) | 80 → 宿主机 8090 |
| 数据库 | SQLite `data/tg_feishu.db`（`DB_URL=sqlite+aiosqlite:////data/tg_feishu.db`）|
| 媒体缓存 | `media_cache/`(容器 `/app/media_cache`) + 容器内 `/app/media_cache` 临时图 |
| 飞书凭证 | 优先 `.env` 的 `FEISHU_APP_ID/SECRET`，**运行时也可存 DB `settings` 表** |
| TG 凭证 | `TG_API_ID/HASH`（登录态：容器内 `.session` 文件，经 `data/` 卷持久化）|
| 代理 | `PROXY_URL`（容器内连宿主机用 `host.docker.internal:1080`）|

- **DB 设置表是唯一持久化配置源**：飞书/ TG 凭证、监听开关、报告群都落 `Setting`
  表；`main.py` 启动 `sync_db_settings_to_config()` 把 DB 值灌进 config 单例。
- 前端与后端跨容器，CORS `allow_origins=["*"]`，调试无需配 host。

### 数据落点（重启不丢的都持久化在哪）

| 数据 | 表 | 说明 |
|---|---|---|
| 频道配置 | `channels` | channel_id(TG)、channel_title/username、feishu_chat_id、enabled、last_forwarded_id |
| 转发规则 | `rules` | 频道级关键词包含/排除、正则、消息类型过滤 |
| 转发日志 | `forward_logs` | status(success/skipped/failed)、error_message、media_type、created_at |
| 群绑定 | `chat_aliases` | name ↔ feishu_chat_id、auto_discovered(自动发现)、enabled |
| 系统设置 | `settings` | 任意 key-value：凭证、listener_enabled、report_chat_id、report_interval_minutes … |
| 用户 | `users` | 后台登录账号（JWT）|

## 3. 日常操作 SOP

### 3.1 构建 / 重建（改代码后）

```bash
cd "D:/WorkBuddy/tg-feishu-forwarder"
docker compose up -d --build backend          # 只重建后端
docker compose up -d --build frontend          # 只重建前端
docker compose up -d --build                   # 全量重建
```

- ⚠️ Bash 工具 cwd 跨命令不保持，一律 `cd` 到 Windows 绝对路径或 `-f` 显式指定。
- 重建后验证：`docker ps --format "{{.Names}} | {{.Status}}"` + `docker logs <c> --tail 20`。
- 前端改完需 `npm run build`（或容器 build 阶段自动 `vite build`），产物进 Nginx。

### 3.2 日志跟踪

```bash
docker compose logs -f backend      # 后端 + 飞书命令 + 转发排障主战场
docker compose logs -f worker       # Celery 媒体上传重试
```

### 3.3 本地前端联调

```bash
cd "D:/WorkBuddy/tg-feishu-forwarder/frontend"
npm install
npm run dev         # Vite dev server（需代理直连 npm registry，见 §8）
```

### 3.4 提交与推送

```bash
git -C "D:/WorkBuddy/tg-feishu-forwarder" add -A
git -C "D:/WorkBuddy/tg-feishu-forwarder" commit -m "..."
git -C "D:/WorkBuddy/tg-feishu-forwarder" -c http.proxy= -c https.proxy= push origin main
```

- push 单次置空代理（本机 `127.0.0.1:1080` 代理会干扰 git smart HTTP → 408）。
- commit message 中文、写清「为什么」（实测场景 + 根因），风格见 `git log`。
- main 直推（个人项目），改完立刻 push。

### 3.5 数据库直接取证

```bash
sqlite3 "D:/WorkBuddy/tg-feishu-forwarder/data/tg_feishu.db" "SQL..."
```

例：查失败日志 `SELECT id,tg_message_id,error_message FROM forward_logs WHERE status='failed' ORDER BY id DESC LIMIT 10;`

## 4. 核心模块（改代码前必须读）

### 4.1 `services/feishu_command.py` — 飞书命令分发（WebSocket 长连接）

- 用 `lark-oapi` SDK 的 `ws.Client` 建**出站 WebSocket**（无需公网 URL），
  线程里 `client.start()` 阻塞运行，断线 10s 重连。
- ⚠️ **SDK 事件线程坑**：lark-oapi 在 import 时捕获了主线程 event loop；
  必须在工作线程里 `asyncio.new_event_loop()` 并覆盖 `ws_client_module.loop`，
  否则命令回调死锁。改这块务必保留此 hack。
- 收到消息 → `_on_message` 同步回调 → 用 `run_coroutine_threadsafe` 抛回主循环
  执行 `_process_command` + `_auto_discover_chat`（自动登记群名）。
- 所有命令带 `tg` 前缀（避免和同机器人其它项目冲突），`tg 帮助`/`tg帮助` 都接受。
- `_handle_fetch` 路由顺序：**先按 TG 频道标题/用户名模糊匹配 → 落空再按群别名
  （alias_resolver.resolve_name）解析飞书群 → 收该群绑定的频道**。

### 4.2 `services/alias_resolver.py` — 群名称解析

- 双路识别：① 命令到达时 `auto_discover` 调飞书 API 读群名登记
  （`auto_discovered=True`）；② 用户 `tg绑定` 或后台手动绑定（手动行优先，绝不
  被自动发现覆盖同名）。
- `resolve_name`：精确匹配（大小写不敏感）→ 双向子串模糊（`IT穿梭机` 能命中
  `穿梭机`）。这是 `tg采集 科技` 能按群路由的根本。

### 4.3 `services/status_reporter.py` — 状态报告

- `build_report(db)`：监听状态 / TG 连接 / 频道数 / 近 24h 与历史累计成功失败/
  最近 3 条错误。标题固定 `📊 青鸟 · 运行状态报告`。
- `send_report(chat_id)`：默认发到 `report_chat_id` 设置项。
- `StatusReportScheduler`：每分钟重读 `report_chat_id` + `report_interval_minutes`，
  到点推送，发成功才更新 `_last_sent`（防止重复刷屏）。

### 4.4 `services/forwarder.py` — 转发主链路（水位线纪律！）

- 水位线 `last_forwarded_id` 是去重核心：小于等于它的消息直接跳过。
- 三路采集：① 实时 `handle_new_message`（Telethon 回调）② `manual_fetch`
  （`tg采集`，拉 `id > last_forwarded_id` 最多 50 条）③ `fetch_by_date`（按日期范围）。
- **铁律（本次修复的重点）**：转发 `except` 分支**只记 failed 日志，绝不
  `await self._update_last_id(...)`**——这样下次采集会重试这条失败消息，而不是跳过。
  源码两处注释已写明（`handle_new_message` 与 `manual_fetch`）。
- 图片走 `feishu_client.upload_image` 自动压缩/转 JPEG，失败重试见 §5.2。

### 4.5 `services/feishu_client.py` — 飞书 Open API 封装

- 自动刷新 tenant_access_token（提前 60s）。
- `send_text/card/rich_text/image/video/file` 全部过 `_check_resp()` 校验
  `code != 0` 抛错（见 §5.1 根因修复）。
- `get_chat_name(chat_id)` 调 `im/v1/chats/{id}` 读群名（自动发现/入群校验用）。
- 媒体上传：`_prepare_image` 用 Pillow 把 >1500px 或 >10MB 的图缩压；飞书拒绝格式
  时 `upload_image` 强制转 JPEG 重试一次。

### 4.6 `services/telegram_client.py` — TG 监听

- `tg_manager` 单例，Telethon 客户端，支持 `start_listener(channel_ids, on_msg)`。
- 监听默认**自动开启**：`main.py` 启动延迟 3s 后 `_auto_start_listener()` 读
  `listener_enabled` 设置，若 `false` 跳过；否则 `start_listener_impl` 拉起所有
  启用频道。

### 4.7 API 路由（`backend/app/api/`）

| 文件 | 关键端点 |
|---|---|
| `telegram.py` | `/api-credentials`、`/login/*`、`/listener/start|stop|status`（start/stop 持久化偏好）|
| `system.py` | `/health`、`/info`、`/report`(预览)、`/report/send`、`/restart`(进程退出靠 Docker 重启) |
| `channels.py` / `rules.py` / `logs.py` / `settings_api.py` | 频道、规则、日志、系统设置 CRUD |
| `aliases.py` | `/api/aliases` 群绑定 CRUD（create 时校验机器人在群内）|
| `auth.py` | 登录/JWT（首次 setup）|

## 5. 飞书群命令（用户侧，改文案必须同步 README + help）

| 命令 | 说明 |
|---|---|
| `tg采集` | 采集所有频道未转发消息 |
| `tg采集 <频道名>` | 按 TG 频道标题模糊采集 |
| `tg采集 <群名称>` | 按群绑定采集（如 `tg采集 科技`）|
| `tg采集 <日期>` / `<开始> <结束>` | 按日期/范围采集（YYYY-MM-DD 或 MM-DD）|
| `tg绑定 <名称> <群ID>` | 绑定群名到飞书群（如 `tg绑定 科技 oc_eff93a6a...`）|
| `tg群列表` | 查看所有群名称绑定 |
| `tg状态 [群名称]` | 发运行状态报告（默认当前群）|
| `tg帮助` | 显示帮助 |

帮助文案在 `feishu_command._send_help`；改命令**必须同步三处**：该文案、
README「飞书群命令」表、前端若有对应说明。

## 6. 已知故障模式与对策（本次排障踩过的坑）

### 6.1 ⚠️ 飞书发送失败被静默吞（根因 230002 机器人不在群内）

- **症状**：飞书群发 `tg采集 科技` 一直没反应，或转发只成功少量、其余消失。
- **根因**：原 `send_*` 不校验响应体 `code`，HTTP 400 + `code=230002`
  （bot 不在群内）被当成成功，消息静默丢弃、水位线照常推进。
- **修复**：所有 `send_*` 加 `_check_resp()`，非 0 抛错 → 进 `failed` 日志 →
  水位线不推进 → 下次采集自动重试。
- **身份澄清（极易混淆）**：本项目用的飞书身份是**企业自建应用**「青鸟」
  （`cli_aafeddf175f85d05`），不是群里的「群机器人/webhook 机器人」，也不是 Aily 智能体。
  两者是不同主体。要让转发工作，必须**把「青鸟」这个自建应用加进目标飞书群**：
  群设置 → 群机器人 → 添加机器人 → 搜应用名「青鸟」→ 添加（且应用需发布版本 +
  开通可用范围含该群）。私聊（p2p）能收命令是因私聊本就绑定该应用。
- **230002 判定口诀**：`tg绑定` 后若提示「机器人不在该群内」，或 `tg状态` 报错、
  转发大量 failed 且 error 含 `230002` → 99% 是应用没进群，去群里加它。

### 6.2 图片 234011 格式无法识别

- TG 的 webp/sticker 飞书有时拒收（`code=234011`）。
- 修复：`upload_image` 捕获后 `force_jpeg=True` 转 JPEG 重试一次（Pillow 处理）。

### 6.3 必须删频道重建才能采集

- **旧行为**：转发失败分支也 `_update_last_id` 推进水位线 → 失败消息被永久跳过，
  只能删频道重建清零水位线。
- **修复（commit 7cbf734）**：失败分支不再推进水位线，删频道重建不再是必需品。

### 6.4 监听自动开启 vs 手动关闭

- 默认 `listener_enabled` 不存在即视为 ON；用户在前端大开关关掉会持久化 `false`，
  重启后 `_auto_start_listener` 尊重该偏好跳过。TG 未授权（没登录）也会跳过并记日志。

## 7. 核心设计约定（改代码勿破）

1. **飞书发送必须校验 code**：任何新 `send_*` 方法都要调 `_check_resp()`，否则失败
   静默、水位线错进，复现 §6.1 灾难。
2. **失败不推进水位线**：`forwarder.py` 两处 except 注释是纪律红线，改采集逻辑时
   失败路径绝不 `_update_last_id`。
3. **群名解析先频道后别名**：`tg采集 <词>` 先匹配 TG 频道，落空才走 `alias_resolver`，
   顺序反了会让"频道名恰好也是群名"的歧义错配。
4. **手动绑定优先于自动发现**：`auto_discover` 遇到 `auto_discovered=False` 的行直接
   返回，不覆盖用户起的名。
5. **配置走 DB 设置表**：飞书/TG 凭证、监听开关、报告群都落 `settings`，启动时
   由 `sync_db_settings_to_config` 注入 config 单例；改配置入口优先走 API/后台，
   不要硬改容器环境变量后还指望它覆盖 DB。
6. **lark-oapi 线程 loop hack**（`feishu_command._run`）：保命代码，动了必死锁。

## 8. 环境注意事项

- **代理**：npm/容器访问外网走 `127.0.0.1:1080`（本机），但容器内连宿主机用
  `host.docker.internal:1080`。`git push` 反而要**置空**代理（见 §3.4）。
- **npm 直连 registry 不通**：需设 `http_proxy/https_proxy=http://127.0.0.1:1080`
  才能 `npm install`（`npm ping` 约 8s 偏慢但可用）。
- 前端主题在 `frontend/src/styles/theme.css`（2026 水果自然风：Bento + 液态玻璃 +
  状态感知色温），改 UI 风格集中在此，别散落到组件内联。

## 9. 当前状态（2026-08-07）

- 4 服务 healthy；监听自动拉起全部启用频道（实测 14~20 个）。
- 品牌化完成「青鸟」+ 图标（`frontend/public/qingniao.png`、`favicon.png`）。
- 三项能力已落地并验证：群名识别绑定、监听自动开启+开关、状态报告（命令+定时）。
- 已清 338 条历史 failed 日志（保留 682 条 success），水位线 bug 已修。
- 待办/观察：
  ① 230002 入群校验已在 `tg绑定`/aliases create 提示，但后台批量导入频道时仍可能
    漏检（靠转发失败暴露）——可考虑导入时批量 `get_chat_name` 预校验；
  ② 容器 build 阶段 `npm ci`/`pip install` 受代理影响，CI 环境需配代理；
  ③ 飞书命令目前仅纯文本（msg_type=text），媒体类命令未支持；
  ④ 状态报告仅纯文本，未用卡片（需要时可扩 `send_card`）。

## 10. 工作风格约定（用户偏好）

- 用户要的是**实测证据**，不是"应该会好"：改完必跑验证（日志/接口/数据库三选一），
  把验证结果贴出来。
- 用户会用真实任务挑战修复（"是不是只修了这个 case"）——修复时主动想同类变体，
  一次修透。
- 汇报格式：结论先行 → 时间线/证据 → 改动文件 → 验证数据。别堆术语。
