# CAS 上传全链路分析

## 概述

CAS（Content-Addressable Storage）用于上传 AI 对话数据到云端分析服务器。客户端对每条 user/assistant 消息计算 SHA-256 哈希，批量上传到服务器的 SQLite 数据库，供后续的 push 摘要生成等功能消费。

## 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Git-ai Daemon (Rust)                        │
│                                                                     │
│  Git Trace2 Hook                                                    │
│       ↓                                                             │
│  Transcript Worker (stream_worker.rs)                               │
│       ↓                                                             │
│  ┌─────────────────┐                                                │
│  │ 1. 过滤事件      │  event_id=5, type∈{user,assistant}            │
│  │ 2. 规范化 JSON   │  serde_json_canonicalizer                     │
│  │ 3. SHA-256 哈希  │  format!("{:x}", hasher.finalize())           │
│  │ 4. 构造 Payload  │  CasSyncPayload{hash, data, metadata}         │
│  └────────┬────────┘                                                │
│           ↓                                                         │
│  TelemetryBuffer.cas_records  (内存缓冲区)                           │
│           ↓                                                         │
│  Flush Loop (每 3 秒)                                               │
│           ↓                                                         │
│  flush_cas() → ApiClient::upload_cas()                              │
│           ↓                                                         │
│  POST /worker/cas/upload  (HTTP, 每批最多 50 条)                    │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   云端服务器 (Python Flask)                          │
│                                                                     │
│  POST /worker/cas/upload                                            │
│       ↓                                                             │
│  ┌─────────────────┐                                                │
│  │ 1. 解析 objects  │  提取 hash, content, metadata                 │
│  │ 2. INSERT OR     │  cas_records 表 (hash UNIQUE)                 │
│  │    IGNORE         │                                               │
│  │ 3. 飞书通知       │  send_feishu_webhook_text()                  │
│  │ 4. 异步转发       │  _async_forward()                            │
│  └─────────────────┘                                                │
│                                                                     │
│  GET /worker/cas/?hashes=...  (按 hash 批量读取)                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 配置入口

### prompt_storage 模式

`src/config.rs:548-568` — 三种模式：

| 值 | 行为 |
|----|------|
| `"default"` | 上传 CAS + 从 git notes 中剥离 prompt |
| `"notes"` | 存入 git notes（不上传 CAS） |
| `"local"` | 跳过，仅本地保留 |

配置优先级（`effective_prompt_storage`）：
1. 如果 repo 在 `exclude_prompts` 列表中 → 强制 `"local"`
2. 如果 `include_prompts_in_repositories` 为空 → 使用全局 `prompt_storage`（向后兼容）
3. 如果 repo 匹配 `include_prompts_in_repositories` → 使用 `prompt_storage`
4. 如果 repo 不匹配 → 使用 `default_prompt_storage`，默认为 `"local"`

## 客户端全链路（10 步）

### 第 1 步：触发时机

**文件**: `src/daemon/stream_worker.rs:1178-1219`

在 transcript worker 的 metrics 持久化之后触发。每次 git 操作产生的 session event（`event_id == 5`）经过 metrics 批量处理后，同批次检查是否需要 CAS 上传。

### 第 2 步：事件过滤

```rust
// stream_worker.rs:1182-1188
for event in &metric_events {
    if event.event_id == 5 {                          // SessionEvent
        if let Some(raw_json) = event.values.get(&"0") {
            let event_type = raw_json.get("type")
                .and_then(|v| v.as_str()).unwrap_or("");
            if event_type != "user" && event_type != "assistant" {
                continue;                              // 只保留 user 和 assistant
            }
```

只上传两类消息：
- `"user"` — 用户提问
- `"assistant"` — AI 回复

过滤掉：`"last-prompt"`、`"task_reminder"`、`"attachment"` 等元数据事件。

### 第 3 步：JSON 规范化

```rust
// stream_worker.rs:1189-1193
match serde_json_canonicalizer::to_string(raw_json) {
    Ok(canonical) => {
        let mut hasher = Sha256::new();
        hasher.update(canonical.as_bytes());
        let hash = format!("{:x}", hasher.finalize());
```

使用 `serde_json_canonicalizer` 保证 JSON 键有序、无多余空白，确保相同内容产生相同哈希。

### 第 4 步：构造 CasSyncPayload

```rust
// stream_worker.rs:1194-1203
let metadata = serde_json::json!({
    "tool": task.tool,           // e.g. "claude"
    "session_id": task.session_id, // e.g. "s_e3085012ba80f7"
}).to_string();

cas_records.push(CasSyncPayload {
    hash,           // SHA-256 hex
    data: canonical, // 规范化的 JSON 字符串
    metadata: Some(metadata),
});
```

**数据结构** (`src/daemon/control_api.rs:180-185`):
```rust
pub struct CasSyncPayload {
    pub hash: String,
    pub data: String,
    pub metadata: Option<String>,  // JSON 字符串
}
```

### 第 5 步：写入内存缓冲区

```rust
// stream_worker.rs:1212-1218
if !cas_records.is_empty() {
    tracing::info!(count = cas_records.len(), ...);
    telemetry.submit_cas_sync(cas_records);
}
```

`submit_cas_sync` (`telemetry_worker.rs:321-325`) 通过 `try_lock()` 非阻塞写入 `TelemetryBuffer.cas_records`。如果锁竞争失败则静默丢弃（best-effort）。

### 第 6 步：定时刷新

**文件**: `src/daemon/telemetry_worker.rs:487-567`

Flush loop 每 **3 秒**（`FLUSH_INTERVAL`）执行一次：

```rust
// telemetry_worker.rs:569-608
fn take_telemetry_flush_snapshot(buffer: ...) {
    let batch = buffer.take();
    // ...
    if !batch.cas_records.is_empty() {
        flush_cas(batch.cas_records);
    }
}
```

`take()` 函数（`telemetry_worker.rs:182-191`）用 `std::mem::take` 原子地取出所有缓冲数据。

### 第 7 步：构建 HTTP 请求

**文件**: `src/daemon/telemetry_worker.rs:1410-1468`

```rust
fn flush_cas(records: Vec<CasSyncPayload>) {
    // 1. 检查登录状态（未登录则跳过）
    if using_default_api && !client.is_logged_in() && !client.has_api_key() {
        return;
    }

    // 2. CasSyncPayload → CasObject（解析 data JSON）
    for record in &records {
        let content: Value = serde_json::from_str(&record.data)?;
        let metadata: HashMap<String, String> =
            serde_json::from_str(&record.metadata)?;
        cas_objects.push(CasObject { content, hash, metadata });
    }

    // 3. 分批上传，每批最多 50 条
    for chunk in cas_objects.chunks(50) {
        let request = CasUploadRequest { objects: chunk.to_vec() };
        client.upload_cas(request)?;
        // 上传成功后删除本地 DB 中的 CAS 记录
        db.delete_cas_by_hashes(&hashes);
    }
}
```

### 第 8 步：HTTP POST

**文件**: `src/api/cas.rs:17-18`

```rust
pub fn upload_cas(&self, request: CasUploadRequest) -> Result<CasUploadResponse> {
    let response = self.context().post_json("/worker/cas/upload", &request)?;
```

请求体格式：
```json
{
  "objects": [
    {
      "content": { "type": "user", "message": { "content": "..." }, ... },
      "hash": "827ca42c3d2c...",
      "metadata": { "tool": "claude", "session_id": "s_xxx" }
    }
  ]
}
```

### 第 9 步：服务端接收

**文件**: `server/local_analytics_server.py:1104-1168`

```python
@app.route('/worker/cas/upload', methods=['POST'])
def cas_upload():
    objects = payload.get('objects', [])
    for obj in objects:
        hash_val = obj.get('hash', '')
        metadata_val = json.dumps(obj.get('metadata', {}))
        content_val = obj.get('content')
        data_val = json.dumps(content_val, ensure_ascii=False)

        cursor.execute('''
            INSERT OR IGNORE INTO cas_records (hash, metadata, data)
            VALUES (?, ?, ?)
        ''', (hash_val, metadata_val, data_val))
```

数据库表结构 (`cas_records`):
| 列 | 类型 | 说明 |
|----|------|------|
| `id` | INTEGER PK | 自增主键 |
| `hash` | TEXT UNIQUE | SHA-256 哈希 |
| `metadata` | TEXT | JSON 字符串（tool, session_id） |
| `data` | TEXT | JSON 字符串（原始消息内容） |
| `created_at` | TIMESTAMP | 创建时间 |

### 第 10 步：通知与转发

```python
# Feishu 通知
if success_count > 0:
    text = f"已收到 {success_count} 条CAS提示词数据"
    send_feishu_webhook_text(text)

# 异步转发到外部服务
_async_forward('/worker/cas/upload', payload)
```

## CAS 数据消费

### Push 摘要生成

**文件**: `server/local_analytics_server.py:978-995`

```python
# 按 session_id 查询 CAS 记录（最近 60 条，按 id DESC）
cur.execute(
    "SELECT data FROM cas_records WHERE metadata LIKE ? ORDER BY id DESC LIMIT 60",
    (f'%{sid}%',))

# 过滤 user/assistant，截取最近 30 轮对话
for row in cur.fetchall():
    d = json.loads(row[0])
    if d.get("type") == "user":
        cas.append(f"[用户]{...}")
    elif d.get("type") == "assistant":
        cas.append(f"[AI]{...}")
```

### Hash 读取接口

```python
@app.route('/worker/cas/', methods=['GET'])
def cas_read():
    hashes = request.args.get('hashes', '').split(',')
    cursor.execute(
        "SELECT hash, data, metadata FROM cas_records WHERE hash IN (...)")
```

## 数据流时序图

```
时间 →

Git Hook 触发
    │
    ├─ Transcript Worker 处理 session events
    │     │
    │     ├─ metrics 持久化到本地 SQLite
    │     │
    │     └─ [prompt_storage=="default"?]
    │           │
    │           ├─ 过滤 user/assistant
    │           ├─ SHA-256 哈希
    │           └─ submit_cas_sync() → TelemetryBuffer
    │
    ├─ [最多 3 秒后] Flush Loop 触发
    │     │
    │     └─ flush_cas()
    │           │
    │           ├─ CasSyncPayload → CasObject
    │           ├─ chunks(50)
    │           └─ POST /worker/cas/upload
    │
    └─ 服务端
          │
          ├─ INSERT OR IGNORE cas_records
          ├─ Feishu 通知
          └─ _async_forward()
```

## 关键文件索引

| 文件 | 行号 | 功能 |
|------|------|------|
| `src/config.rs` | 548-609 | `prompt_storage` 配置和 `effective_prompt_storage` |
| `src/daemon/stream_worker.rs` | 1178-1219 | CAS 事件过滤、哈希、提交 |
| `src/daemon/control_api.rs` | 178-185 | `CasSyncPayload` 数据结构 |
| `src/daemon/telemetry_worker.rs` | 57-62 | `TelemetryBuffer` 缓冲区 |
| `src/daemon/telemetry_worker.rs` | 157-159 | `ingest_cas()` 写入缓冲 |
| `src/daemon/telemetry_worker.rs` | 182-191 | `take()` 取出缓冲 |
| `src/daemon/telemetry_worker.rs` | 321-325 | `submit_cas_sync()` 同步提交 |
| `src/daemon/telemetry_worker.rs` | 487-567 | Flush Loop 定时刷新 |
| `src/daemon/telemetry_worker.rs` | 1410-1468 | `flush_cas()` HTTP 上传 |
| `src/api/types.rs` | 85-113 | `CasObject`, `CasUploadRequest/Response` |
| `src/api/cas.rs` | 17-58 | `upload_cas()` API 调用 |
| `server/local_analytics_server.py` | 60 | `DB_PATH` 数据库路径 |
| `server/local_analytics_server.py` | 1046-1054 | `cas_records` 表结构 |
| `server/local_analytics_server.py` | 1104-1168 | `POST /worker/cas/upload` 接收 |
| `server/local_analytics_server.py` | 1170-1200 | `GET /worker/cas/` 读取 |
| `server/local_analytics_server.py` | 978-995 | Push 摘要中的 CAS 消费 |
