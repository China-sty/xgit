# Local Analytics Server

小鹏汽车座舱平台研发部 AI 编码效能分析服务端，接收 git-ai 客户端上报的提示词（CAS）和效能指标（Metrics）数据，提供组织维度的 AI 渗透率统计 API。

## 技术栈

- Python 3 + Flask（HTTP 服务，端口 5000）
- SQLite（本地存储，WAL 模式）
- CSV 文件管理组织架构和黑名单

## 目录结构

```
server/
├── local_analytics_server.py   # 主程序
├── server.log                  # 运行日志
├── git_ai_local_analytics.db   # SQLite 数据库
├── 座舱平台研发部人员明细-0415_数据表.csv  # 组织架构数据
├── member_blacklist.csv        # 成员黑名单
└── commit_blacklist.csv        # 提交黑名单
```

## 核心逻辑

### 1. 数据接收

| API | 方法 | 说明 |
|-----|------|------|
| `/worker/cas/upload` | POST | 接收 AI 提示词数据，存入 `cas_records` 表 |
| `/worker/cas/` | GET | 按 hash 批量读取 CAS 对象 |
| `/worker/metrics/upload` | POST | 接收效能指标事件，存入 `metrics_events` 表 |

### 2. 统计查询

| API | 说明 |
|-----|------|
| `/api/stats/summary` | 部门级 AI 编码汇总（AI 占比、代码行数、提交数、活跃人数） |
| `/api/stats/team-ranking` | 子团队 AI 占比排行 |
| `/api/stats/team-detail` | 团队明细（分页） |
| `/api/stats/daily-trend` | 每日趋势（按日期聚合） |
| `/api/organizations` | 组织层级树查询 |

### 3. 黑名单管理

| API | 方法 | 说明 |
|-----|------|------|
| `/api/blacklist` | GET | 查询成员黑名单 |
| `/api/blacklist` | POST | 添加成员到黑名单 |
| `/api/blacklist/<prefix>` | DELETE | 移除成员黑名单 |
| `/api/commit-blacklist` | GET/POST | 提交黑名单管理 |
| `/api/commit-blacklist/<sha>` | DELETE | 移除提交黑名单 |

### 4. 数据转发

收到客户端数据后，通过 `_async_forward()` 以 fire-and-forget 方式异步转发到云端：

- **测试环境**：`FORWARD_TEST_URL` 环境变量
- **生产环境**：`FORWARD_PROD_URL` 环境变量

### 5. 数据表结构

**cas_records**（提示词存储）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 自增主键 |
| hash | TEXT | CAS hash，唯一索引 |
| metadata | TEXT | JSON 元数据 |
| data | TEXT | 提示词内容 JSON |
| created_at | TIMESTAMP | 创建时间 |

**metrics_events**（效能事件）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 自增主键 |
| timestamp | INTEGER | Unix 时间戳 |
| event_type | INTEGER | 事件类型（1=提交事件） |
| values_json | TEXT | 指标值 JSON |
| attributes_json | TEXT | 属性 JSON（author、commit_sha 等） |
| created_at | TIMESTAMP | 创建时间 |

### 6. 统计计算逻辑

- 从 `metrics_events` 查询时间范围内的提交事件（event_type=1）
- 通过 `attributes_json.2`（author）匹配组织成员
- 取 `values_json.5[0]` 作为 AI 代码行数，`values_json.2` 为总新增行数
- AI 编码占比 = AI 行数 / 总新增行数 × 100%
- 排除 base_commit_sha 为 "initial" 的提交和黑名单中的提交/成员

## 命令行

```bash
# 启动服务（默认）
python local_analytics_server.py serve

# 清空数据库并重建 CSV
python local_analytics_server.py reset

# 基于当前数据库重建统计 CSV
python local_analytics_server.py rebuild-csv

# 测试组织树和统计服务
python local_analytics_server.py test

# 添加提交到黑名单
python local_analytics_server.py add-commit-blacklist <commit_sha>
```

## 部署

### 环境要求

```bash
pip install flask
```

### 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `FORWARD_TEST_URL` | 测试环境转发地址 | `http://logan-gateway.test.logan.xiaopeng.local/xp-ai-coding` |
| `FORWARD_TEST_API_KEY` | 测试环境 API Key | `xp-ai-coding-2026-api-key` |
| `FORWARD_PROD_URL` | 生产环境转发地址 | `https://gic-ai-center.xiaopeng.com` |
| `FORWARD_PROD_API_KEY` | 生产环境 API Key | `xp-ai-coding-2026-api-key` |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook | 用于提交通知 |

### 部署步骤

```bash
# 1. 上传文件到服务器
scp server/local_analytics_server.py user@server:/path/to/acsp/

# 2. 上传组织架构 CSV
scp 座舱平台研发部人员明细-0415_数据表.csv user@server:/path/to/acsp/

# 3. SSH 到服务器
ssh user@server

# 4. 安装依赖
pip install flask

# 5. 停止旧进程
pkill -f local_analytics_server.py

# 6. 启动服务
cd /path/to/acsp
nohup python local_analytics_server.py serve > server.log 2>&1 &

# 7. 验证服务
curl http://localhost:5000/api/organizations
curl -X POST http://localhost:5000/worker/cas/upload \
  -H "Content-Type: application/json" \
  -d '{"objects":[{"hash":"test","content":{"msg":"hello"},"metadata":{}}]}'
```

### 客户端配置

在 `~/.git-ai/config.json` 中将 `api_base_url` 指向此服务：

```json
{
  "api_base_url": "http://<服务器地址>:5000"
}
```

设置后 CAS 数据无需认证即可上传（自定义 URL 免认证），Metrics 数据需要配置 `api_key` 或 OAuth 登录。

## 端口

- 服务监听：`0.0.0.0:5000`
- 启动时会自动检测端口占用，如被占用则报错退出
