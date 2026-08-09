# 青鸟 · TG→飞书消息转发系统

将 Telegram 频道的图文消息自动化、选择性地转发至飞书群组（视频不转发，改投递文案 + 回看提示）。

> 青鸟衔信，穿山越岭——TG 消息一站直达飞书。

## 快速开始

### 1. 配置环境变量

编辑 `.env` 文件，填入以下凭证：

```bash
# Telegram API (从 https://my.telegram.org 获取)
TG_API_ID=你的API_ID
TG_API_HASH=你的API_HASH

# 飞书自建应用 (从飞书开放平台获取)
FEISHU_APP_ID=cli_xxxxxx
FEISHU_APP_SECRET=你的密钥

# 代理设置 (如需翻墙访问TG)
# Docker 容器中连接宿主机代理使用 host.docker.internal
PROXY_URL=socks5://host.docker.internal:7890
```

### 2. 启动服务

```bash
docker compose up -d
```

### 3. 访问管理后台

- **前端管理界面**: http://localhost:8090
- **后端 API 文档 (Swagger)**: http://localhost:8010/docs

### 4. 使用流程

1. 打开管理后台 http://localhost:8090
2. 进入「TG 登录」页面，设置 API ID/Hash，完成手机号验证码登录
3. 进入「频道管理」，点击「从TG导入」选择要监听的频道
4. 为每个频道配置飞书群 ID（`oc_xxxxx` 格式）
5. 进入「转发规则」，按需配置关键词过滤、正则匹配等规则
6. 回到「TG 登录」或「仪表盘」，点击「启动监听」

## 服务架构

| 服务 | 端口 | 说明 |
|------|------|------|
| frontend | 8090 | Vue 3 + Element Plus 管理后台 |
| backend | 8010 | FastAPI 后端 + Telethon TG 监听 |
| worker | - | Celery 异步任务 (媒体上传) |
| redis | - | 消息队列 (仅容器内部) |

## 功能特性

- **多频道监听**: 支持同时监听多个 TG 频道，分别转发到不同飞书群
- **监听自动开启**: 服务启动后监听自动运行，仪表盘大开关一键关闭/开启（偏好持久化）。实时监听任务若异常退出，健康看门狗每 30s 自动拉起
- **状态分两态**: `tg状态`/`tg监听` 区分「实时监听」与「补抓兜底」——实时停止但补抓开启时显示"⚠️ 实时已停（补抓保底投递中）"，状态不再与"能收消息"矛盾
- **群名称绑定**: 飞书群ID ↔ 群名称智能识别——机器人所在群自动登记；支持手动绑定（`tg绑定 科技 oc_xxx`）；`tg采集 科技` 按群路由采集
- **运行状态报告**: `tg状态 [群名称]` 即时推送；支持定时推送到指定群（系统设置中配置间隔）
- **规则引擎**: 关键词包含/排除、正则匹配、消息类型过滤
- **媒体转发**: 图片、文件自动下载并上传至飞书。若飞书机器人未加入目标群，转发会记 `skipped` 并提示"请将其加入该群"，不再无限重试刷屏（错误码 230002）
- **视频策略（不转发）**: 飞书对 TG 视频的编码/体积限制严格（`msg_type: video` 非法，`media` 亦常被拒），且大文件跨 DC 下载会拖死补抓循环。视频消息**不下载不上传**，改为投递「原文案 + 📹 此信息包含视频，请回 TG 观看原视频！ + t.me 原帖链接」
- **代理支持**: SOCKS5/HTTP 代理，解决网络连通问题
- **异步队列**: Celery + Redis 处理大文件，失败自动重试
- **日志审计**: 完整的转发日志记录与统计
- **仪表盘统计**: 按飞书群分组展示转发量（今日/本周/本月/累计），可展开频道明细，支持成功率可视化。新增群或绑定别名后自动同步，无需手动配置
- **可视化后台**: 全动态配置，无需重启即可调整；2026 水果自然风 UI（Bento 布局 + 液态玻璃 + 状态感知色温）

## 飞书群命令

| 命令 | 说明 |
|------|------|
| `tg采集` | 采集所有频道的未转发消息 |
| `tg采集 <频道名>` | 采集指定频道（模糊匹配标题/用户名） |
| `tg采集 <群名称>` | 按群绑定采集该群关联的频道 |
| `tg采集 <名称> <日期> [结束]` | 按日期/范围采集 |
| `tg订阅 <频道名> <群名称>` | 加入并订阅 TG 频道 + 绑定转发群（根治零转发；频道名可为 @用户名 或 t.me 链接） |
| `tg暂停 <频道名>` | 暂停该频道转发 |
| `tg恢复 <频道名>` | 恢复已暂停的频道 |
| `tg移除 <频道名>` | 从系统中删除频道 |
| `tg监听` / `tg监控` | 查看实时监听状态（`tg监控` 为等价别名） |
| `tg监听 开启` / `tg监听 关闭` | 开启 / 关闭实时监听（`tg监控` 等同，如 `TG监控 开启`） |
| `tg重启` | 重启后端服务（Docker 自动拉起） |
| `tg补抓 <分钟>` | 周期补抓间隔（0=关闭，默认 2；无参数查看当前值） |
| `tg登录 <手机号>` | 发送登录验证码到 TG（掉线重登） |
| `tg验证码 <6位码>` | 输入验证码完成登录 |
| `tg密码 <两步密码>` | 两步验证密码登录（验证码后需要时使用） |
| `tg绑定 <名称> <群ID>` | 绑定群名称到飞书群（如 `tg绑定 科技 oc_a879...`） |
| `tg群列表` | 查看所有群名称绑定 |
| `tg状态 [群名称]` | 发送运行状态报告（默认当前群） |
| `tg清理 日志` | 预览将清空的转发日志数量（不删除） |
| `tg清理 日志 确认` | 清空全部转发历史记录（不可恢复） |
| `tg统计 [今日/本周/本月/累计]` | 群消息转发统计概览，按群分组展示转发量（默认今日） |
| `tg帮助` | 显示帮助 |

> 命令前缀 `tg` 大小写随意：`TG帮助` / `tg 帮助` / `Tg帮助` / `tG帮助` 均等效。

## 常用命令

```bash
# 启动
docker compose up -d

# 查看日志
docker compose logs -f backend
docker compose logs -f worker

# 重启服务
docker compose restart

# 停止
docker compose down

# 重新构建 (代码修改后)
docker compose up -d --build

# 只重建后端相关服务（backend 与 worker 共用同一镜像，必须一起重建）
docker compose up -d --build --no-deps backend worker
```

> ⚠️ **铁律：改 Python 代码后 `restart` 无效，必须 rebuild。**
> `backend/app` **不是** bind mount —— 代码在 `docker build` 时 COPY 进镜像，
> `docker compose restart backend` 跑的仍是旧代码。只有 `--build` 才生效。
> `worker` 与 `backend` 共用同一镜像，改公共代码时两个都要重建。
> 验证是否生效：`docker compose exec -T backend grep -n "<你的新代码>" /app/app/services/xxx.py`

## 全量指令测试

`tools/tg_full_chain_test.py` 在 backend 容器内跑全部 TG 指令的链路测试（临时 SQLite + mock 飞书/TG/监听/采集外部依赖，零副作用）：

```bash
# 把测试脚本放进运行中的 backend 容器并执行
docker cp tools/tg_full_chain_test.py tg-feishu-forwarder-backend-1:/tmp/
docker compose exec -T backend sh -c "PYTHONPATH=/app python -u /tmp/tg_full_chain_test.py"
```

覆盖：全 16 组指令（`tg帮助/采集/暂停/恢复/移除/监听/监控/重启/登录/验证码/密码/补抓/订阅/清理/绑定/群列表/状态`）× 大小写前缀变体（`TG/tg/Tg/tG`）× 代表性参数，断言每条指令都能正确路由并执行、无未捕获异常。全部 PASS 才允许发布。

## 飞书应用配置

1. 前往 [飞书开放平台](https://open.feishu.cn/) 创建企业自建应用
2. 获取 App ID 和 App Secret
3. 在「应用能力」中开启「机器人」
4. 配置权限: `im:message:send_as_bot` (发消息)、`im:resource` (上传媒体)
5. 将机器人添加到目标群聊，获取群聊 ID (`oc_xxxxx`)

## Telegram API 获取

1. 访问 https://my.telegram.org
2. 登录后进入「API development tools」
3. 创建应用获取 `api_id` 和 `api_hash`
