# Git AI 意图归属分析 Agent 项目文档

## 1. 项目背景与业务价值
传统的代码追溯（如 `git blame`）只能追踪“代码的物理行是谁写的”，而在现代车载系统（如 SystemUI、多屏交互 XAppMode）开发中，业务逻辑错综复杂，往往需要追踪**“某个业务意图/功能模块是由谁主要负责或近期在维护”**。

本项目的核心价值在于：**将开发者与 AI 的自然语言对话（Prompt）、代码提交日志（Commit Message）作为知识源，通过 Agentic RAG（代理式检索增强生成）架构，实现基于“自然语言业务意图”的智能责任人反推与归属查询。**

---

## 2. 核心架构设计

本项目采用了 **大模型意图提取 + Milvus 向量混合检索 + 大模型综合推理** 的三段式架构。

### 2.1 架构数据流向图

```mermaid
graph TD
    subgraph 1. 数据同步层 (Data Sync)
        A[SQLite: git_ai_local_analytics.db] -->|提取/清洗| B(清洗规则: 剔除无用埋点, 仅保留 Commit/Prompt)
        B -->|文本拆分| C[阿里云 Embedding API text-embedding-v4]
        C -->|向量+Metadata| D[(Milvus Lite: milvus_demo.db)]
    end

    subgraph 2. 在线服务层 (Agent Server - 端口 3000)
        E[用户提问: '胶囊是谁负责'] -->|POST /chat| F[LangGraph / 状态机调度]
        
        F -->|Node 1: 意图提取| G[Kimi LLM: 提取实体 '胶囊']
        G -->|Node 2: 向量检索| H[阿里云 Embedding API: '胶囊' -> 向量]
        H -->|查询| D
        D -->|返回 Top K 客观证据| I[组装证据文本]
        I -->|Node 3: 综合推理| J[Kimi LLM: 分析客观证据, 排除异常]
    end

    subgraph 3. 客户端 (Client)
        J -->|JSON 返回结论| K[Windows 终端 / 飞书机器人]
    end
```

---

## 3. 核心组件与文件说明

当前项目部署于 Linux 服务器的 `~/acsp` 目录下，包含以下核心文件：

### 3.1 `sync_vector.py` (数据向量化同步脚本)
* **职责**：负责从远端 SQLite 数据库中读取 `cas_records`（对话记录）和 `metrics_events`（效能事件），进行数据清洗，调用阿里云 API 进行向量化，并持久化到 Milvus 中。
* **核心逻辑（数据清洗与切分最佳实践）**：
  * **过滤噪音**：丢弃缺少开发者标识（如 `Unknown`）和缺少核心语义（如没有 `files_changed` 或 `commit_message`）的无效埋点数据。
  * **分离 Metadata**：将自然语言（如提交说明、修改的文件名）作为 `page_content` 参与向量化计算；将客观事实（开发者名称、时间、行数）作为 `metadata` 附加，避免向量空间被无意义的标量数据污染。
* **执行频率**：仅在数据有新增时按需（或定时增量）执行，**无需每次启动服务时执行**。

### 3.2 `agent_demo.py` (Agent 在线服务入口)
* **职责**：基于 FastAPI 提供的 HTTP 在线问答接口，负责串联大模型与 Milvus 知识库，完成从“理解”到“查证”再到“推理”的闭环。
* **核心逻辑（性能与检索优化）**：
  * **全局单例长连接**：在 FastAPI 启动时提前将 `milvus_demo.db` 加载到内存中，避免每次请求时的冷启动和 I/O 延迟，将查询耗时从分钟级降至秒级。
  * **状态机调度**：手工实现了一个轻量级的状态机，控制 `提取 -> 检索 -> 综合` 三个节点的有序执行与容错降级。
  * **智能综合推理**：在最后阶段，LLM 不仅会统计命中次数，还会识别别名（如合并 `shaoty1` 和其邮箱账号的权重），并严格按照证据输出，防止幻觉。

### 3.3 `requirements_agent.txt` (环境依赖清单)
* 包含了运行该架构所需的关键 Python 包：`fastapi`, `uvicorn`, `langchain-openai`, `langchain-milvus`, `milvus-lite` (轻量级单机向量引擎), `pymilvus`, `tiktoken`。

---

## 4. 关键接口与配置说明

### 4.1 大模型配置 (LLM)
* **用途**：意图提取与综合推理总结。
* **供应商**：扶摇 AI 网关 (`https://fuyao-ai-gateway.xiaopeng.link/v1`)
* **模型**：`kimi-k2.6`
* **鉴权方式**：自定义 HTTP Header `x-api-key`。

### 4.2 向量模型配置 (Embedding)
* **用途**：文本向量化，计算语义相似度。
* **供应商**：阿里云模型服务 (`https://ws-36x1urn0k5njxup5.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings`)
* **模型**：`text-embedding-v4`

### 4.3 向量数据库 (Vector DB)
* **引擎**：Milvus Lite
* **存储方式**：本地文件持久化 (`milvus_demo.db`)
* **优势**：完美兼容生产环境的 Milvus 分布式集群 API，支持标量过滤与高并发检索，远胜于 FAISS 等本地库。

---

## 5. 常见运维与测试命令

**1. 初始化/更新知识库 (按需执行)**
```bash
export PYTHONWARNINGS="ignore"
python3 sync_vector.py
```

**2. 启动 Agent 在线服务 (后台常驻)**
```bash
export PYTHONWARNINGS="ignore"
nohup python3 agent_demo.py > agent_server.log 2>&1 &
```

**3. 客户端发起测试请求**
```powershell
python -c "import requests; print(requests.post('http://10.99.33.39:3000/chat', json={'query': '我想知道胶囊是谁负责'}, headers={'Content-Type': 'application/json; charset=utf-8'}).json()['answer'])"
```

---

## 6. 未来演进与优化方向 (Roadmap)

1. **增量同步 (Incremental Sync)**：目前的同步脚本是全量覆盖。未来应改造为记录上次同步的 `created_at` 时间戳，配合 Linux `crontab` 每天凌晨仅同步新增数据，实现 Token 零损耗与自动化。
2. **混合检索 (Hybrid Search)**：在 Milvus 查询时，利用 Metadata 引入标量过滤（如：只检索最近 3 个月的数据，或排除某些离职员工），提高查询精准度。
3. **企业级接入**：将 `0.0.0.0:3000` 接口封装后接入飞书自定义机器人后台，让团队成员可以直接在群聊中 @ 机器人查询业务模块负责人。
