# 服务端架构

## 架构总览

```mermaid
flowchart TB
    subgraph Clients["🖥️ git-ai 客户端"]
        GC["git-ai daemon<br/>(hook + CommitLink)"]
        CLI["git-ai CLI<br/>(upload-head-metrics)"]
    end

    subgraph Server["🖥️ 本地分析服务器 (local_analytics_server.py)"]
        subgraph Ingest["📥 数据接入"]
            CAS["POST /worker/cas/upload<br/>CAS 提示词数据"]
            MET["POST /worker/metrics/upload<br/>效能指标数据"]
        end

        subgraph Logic["⚙️ 业务逻辑"]
            BL["黑名单过滤<br/>(开发者 + Commit)"]
            FW["数据转发<br/>(async)"]
            CL["CommitLink 检测<br/>(event_type=8)"]
        end

        subgraph Summary["🤖 AI 摘要"]
            GEN["_generate_push_summary()"]
            LLM["DeepSeek API<br/>(deepseek-chat)"]
            FS["飞书 Webhook<br/>(卡片通知)"]
        end

        subgraph API["📊 统计 API"]
            S_S["/api/stats/summary"]
            S_R["/api/stats/team-ranking"]
            S_D["/api/stats/team-detail"]
            S_T["/api/stats/daily-trend"]
            O["/api/organizations"]
            MGR["/api/blacklist<br/>/api/commit-blacklist<br/>/api/summary/circuit-breaker"]
        end
    end

    subgraph Storage["💾 数据存储"]
        SQLITE[("SQLite<br/>git_ai_local_analytics.db")]
        CSV["CSV 文件<br/>黑名单 / 组织架构"]
    end

    subgraph Upstream["☁️ 上游服务"]
        TEST["测试环境<br/>logan-gateway.test"]
        PROD["生产环境<br/>gic-ai-center.xiaopeng.com"]
    end

    subgraph External["🌐 外部服务"]
        DEEPSEEK["DeepSeek API"]
        FEISHU["飞书机器人"]
    end

    GC -->|"push 时触发"| CL
    CLI -->|"metrics 批量上传"| MET
    GC -->|"prompt 数据上传"| CAS

    CAS --> BL --> SQLITE
    MET --> BL --> SQLITE
    BL --> FW
    FW -->|"FORWARD_TEST_URL"| TEST
    FW -->|"FORWARD_PROD_URL"| PROD

    CL -->|"提取 session_ids + diff_stat"| GEN
    GEN -->|"查询 CAS 对话记录"| SQLITE
    GEN -->|"API Key: SUMMARY_LLM_KEY"| DEEPSEEK
    GEN -->|"结果存入"| SQLITE
    GEN -->|"SUMMARY_FEISHU_URL"| FEISHU

    API --> SQLITE
    API --> CSV
```

## 数据流详解

### 1. Push → CommitLink → 摘要

```mermaid
sequenceDiagram
    participant Dev as 👤 开发者
    participant GitAI as git-ai daemon
    participant Server as analytics server
    participant SQLite as SQLite DB
    participant LLM as DeepSeek API
    participant Feishu as 飞书机器人

    Dev->>GitAI: git push
    GitAI->>GitAI: push detected<br/>enable_push_summary?
    GitAI->>GitAI: submit_commit_link_on_push()<br/>读取 reflog 获取 push range<br/>读取 git notes 获取 session_ids<br/>获取 git diff --stat
    
    Note over GitAI: 条件: enable_push_summary=true<br/>git_hooks_enabled=true

    GitAI->>Server: POST /worker/metrics/upload<br/>event_type=8 (CommitLink)<br/>{commit_sha, session_ids, branch, diff_stat, message, author}

    Server->>Server: 检测 event_type == 8
    Server->>SQLite: 查询 cas_records<br/>(按 session_ids 查对话记录)
    SQLite-->>Server: AI 对话历史

    Server->>LLM: POST /chat/completions<br/>DeepSeek API<br/>prompt 包含 commit 信息 + 对话摘要
    LLM-->>Server: JSON {one_liner, changes, why, conversation}

    Server->>SQLite: INSERT INTO push_summaries
    Server->>Feishu: POST 飞书卡片消息<br/>摘要通知

    Feishu-->>Dev: 📱 收到提交摘要通知
```

### 2. 配置开关

CommitLink 摘要功能需要两个开关同时开启：

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_push_summary` | `~/.git-ai/config.json` 顶层 | `false` | 控制是否在 push 时提交 CommitLink 事件 |
| `feature_flags.git_hooks_enabled` | `~/.git-ai/config.json` 内 | `false` | git hooks 是否启用 |

```json
{
  "enable_push_summary": true,
  "feature_flags": {
    "git_hooks_enabled": true
  }
}
```

## 数据库表结构

### metrics_events（效能事件）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| timestamp | INTEGER | Unix 时间戳 |
| event_type | INTEGER | 事件类型（1=提交, 8=CommitLink） |
| values_json | TEXT | 事件值 JSON |
| attributes_json | TEXT | 事件属性 JSON |

### cas_records（CAS 提示词数据）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| hash | TEXT UNIQUE | CAS 对象哈希 |
| metadata | TEXT | 元数据 JSON（含 session_id） |
| data | TEXT | 提示词内容 JSON |

### push_summaries（提交摘要）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| commit_sha | TEXT UNIQUE | 提交 SHA |
| branch | TEXT | 分支名 |
| session_ids | TEXT | 关联的会话 ID 列表 (JSON) |
| one_liner | TEXT | AI 生成的单句摘要 |
| conversation_summary | TEXT | 对话摘要 |
| changes_summary | TEXT | 改动摘要 |
| diff_stat | TEXT | git diff --stat 原始输出 |
| why | TEXT | 改动原因分析 |
| created_at | TIMESTAMP | 创建时间 |

## API 路由汇总

### 数据接入

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/worker/cas/upload` | CAS 提示词批量上传 |
| GET | `/worker/cas/` | 按 hash 查询 CAS 对象 |
| POST | `/worker/metrics/upload` | 效能指标批量上传 |

### 统计查询

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/api/stats/summary` | `dept_id`, `start`, `end` | 部门统计概览 |
| GET | `/api/stats/team-ranking` | `dept_id`, `start`, `end` | 团队排名 |
| GET | `/api/stats/team-detail` | `dept_id`, `start`, `end`, `group_id?` | 团队详情 |
| GET | `/api/stats/daily-trend` | `dept_id`, `start`, `end` | 每日趋势 |
| GET | `/api/organizations` | `org_id?` | 组织架构查询 |

### 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/blacklist` | 开发者黑名单管理 |
| DELETE | `/api/blacklist/<email_prefix>` | 移除黑名单 |
| GET/POST | `/api/commit-blacklist` | Commit 黑名单管理 |
| DELETE | `/api/commit-blacklist/<sha>` | 移除 Commit 黑名单 |
| GET/POST | `/api/summary/circuit-breaker` | LLM 摘要熔断开关 |

## 环境变量 (.env)

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `FEISHU_WEBHOOK_URL` | 飞书通知 Webhook | `https://open.feishu.cn/open-apis/bot/v2/hook/...` |
| `FORWARD_TEST_URL` | 测试环境转发地址 | `http://logan-gateway.test.logan.xiaopeng.local/xp-ai-coding` |
| `FORWARD_TEST_API_KEY` | 测试环境 API Key | `xp-ai-coding-2026-api-key` |
| `FORWARD_PROD_URL` | 生产环境转发地址 | `https://gic-ai-center.xiaopeng.com` |
| `FORWARD_PROD_API_KEY` | 生产环境 API Key | `xp-ai-coding-2026-api-key` |
| `SUMMARY_LLM_KEY` | AI 摘要 LLM Key | **必填**，未设置则跳过摘要 |
| `SUMMARY_LLM_URL` | AI 摘要 LLM 地址 | `https://api.deepseek.com/v1` |
| `SUMMARY_LLM_MODEL` | AI 摘要模型 | `deepseek-chat` |
| `SUMMARY_FEISHU_URL` | 摘要飞书通知 URL | 可选，未设置则跳过飞书推送 |

## 部署运行

服务器脚本位置：`Y:\acsp\local_analytics_server.py`

配置文件：`Y:\acsp\agent_service\.env`

日志文件：`Y:\acsp\server.log`

数据库文件：`Y:\acsp\git_ai_local_analytics.db`
