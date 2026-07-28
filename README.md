# TG→飞书消息转发系统

将 Telegram 频道的图文、视频消息自动化、选择性地转发至飞书群组。

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
- **规则引擎**: 关键词包含/排除、正则匹配、消息类型过滤
- **媒体转发**: 图片、视频、文件自动下载并上传至飞书
- **代理支持**: SOCKS5/HTTP 代理，解决网络连通问题
- **异步队列**: Celery + Redis 处理大文件，失败自动重试
- **日志审计**: 完整的转发日志记录与统计
- **可视化后台**: 全动态配置，无需重启即可调整

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
```

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
