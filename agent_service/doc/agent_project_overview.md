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

## 3. 核心组件与项目结构

当前项目部署于 Linux 服务器的 `~/acsp/agent_service`（本地开发路径为 `D:\xgit\agent_service`），采用标准 LangChain 现代化工程结构：

```text
your_langchain_project/
├── agents/
│   └── simple_agent.py      # Agent 状态机流转逻辑（包含 extract, query_db, synthesize 节点）
├── chains/                  # (预留) 链的组装与复用
├── tools/                   # (预留) 工具定义与封装
├── memory/                  # (预留) 记忆管理
├── retriever/
│   ├── embeddings.py        # AliyunEmbeddings 阿里云向量化封装类
│   ├── vector_store.py      # Milvus 全局单例连接池管理 (解决冷启动耗时)
│   └── sync_vector.py       # 离线增量清洗与向量化同步脚本 (复用了 embeddings)
├── schemas/
│   └── state.py             # 数据模型与类型定义 (如 AgentState, ChatRequest)
├── prompts/
│   └── agent_prompts.py     # ChatPromptTemplate 提示词模板统一管理
├── config/
│   └── settings.py          # 环境变量及所有 API KEY、URL、Model 的配置中心
├── callbacks/               # (预留) 回调与可观测性
├── evaluations/             # (预留) 评估与测试
├── utils/                   # (预留) 工具函数
├── main.py                  # FastAPI 在线服务入口 (uvicorn 启动)
└── requirements_agent.txt   # 依赖文件
```

### 3.1 `retriever/sync_vector.py` (数据增量向量化同步脚本)
* **职责**：负责从远端 SQLite 数据库中读取 `cas_records`（对话记录）和 `metrics_events`（效能事件），进行数据清洗，调用阿里云 API 进行向量化，并持久化到 Milvus 中。
* **核心逻辑（数据清洗与切分最佳实践）**：
  * **增量同步机制**：记录最后一次同步时间到 `last_sync_time.txt`，支持按 Batch 并发调用阿里云 Embedding API，极大缩短了同步耗时。
  * **过滤噪音**：丢弃缺少开发者标识（如 `Unknown`）和缺少核心语义（如没有 `files_changed` 或 `commit_message`）的无效埋点数据。
  * **分离 Metadata**：将自然语言（如提交说明、修改的文件名）作为 `page_content` 参与向量化计算；将客观事实（开发者名称、时间、行数）作为 `metadata` 附加，避免向量空间被无意义的标量数据污染。
* **执行频率**：仅在数据有新增时按需（或定时增量）执行，**无需每次启动服务时执行**。

### 3.2 `main.py` & `agents/simple_agent.py` (Agent 在线服务入口)
* **职责**：基于 FastAPI 提供的 HTTP 在线问答接口，负责串联大模型与 Milvus 知识库，完成从“理解”到“查证”再到“推理”的闭环。
* **核心逻辑（性能与检索优化）**：
  * **全局单例长连接 (在 `retriever/vector_store.py` 中)**：在 FastAPI 启动时提前将 `milvus_demo.db` 加载到内存中，避免每次请求时的冷启动和 I/O 延迟，将查询耗时从分钟级降至秒级。
  * **流式打字机响应 (Streaming SSE)**：在 `ChatOpenAI` 开启 `streaming=True`，并在 `main.py` 返回 `StreamingResponse`。通过前端/测试脚本做 20ms 的人工平滑输出，解决了企业网关分块返回导致的卡顿，彻底消除了 35 秒的漫长等待。
  * **状态机调度**：虽然未直接使用第三方 LangGraph 包（由于 Python 3.8.10 兼容性问题），但手工实现了一套**原生 DAG 有向无环图状态机**，支持意图路由（Router）与反思重试（Reflexion），控制 `提取 -> 检索 -> 综合` 三个节点的有序执行与容错降级。
  * **智能综合推理**：在最后阶段，LLM 不仅会统计命中次数，还会识别别名（如合并 `shaoty1` 和其邮箱账号的权重），并严格按照证据输出，防止幻觉。

### 3.3 `requirements_agent.txt` (环境依赖清单)
* 包含了运行该架构所需的关键 Python 包：`fastapi`, `uvicorn`, `langchain-openai`, `langchain-milvus`, `milvus-lite` (轻量级单机向量引擎), `pymilvus`, `tiktoken`。

---

## 4. 关键接口与配置说明

所有的配置已抽离至 `config/settings.py`，并通过环境变量与 `.env` 文件实现脱敏保护。

### 4.1 安全配置与 `.env` 机制
为防止敏感 API Key 泄露到代码库中：
* 所有真实密钥（LangSmith, 阿里云, LLM）必须配置在部署服务器的 `agent_service/.env` 文件中。
* `.env` 文件已被 `.gitignore` 保护，绝对禁止提交。
* 开发者可参考 `.env.example` 了解配置项模板。

### 4.2 大模型配置 (LLM)
* **用途**：意图提取与综合推理总结。
* **供应商**：扶摇 AI 网关 (`https://fuyao-ai-gateway.xiaopeng.link/v1`)
* **模型**：`kimi-k2.6`
* **鉴权方式**：自定义 HTTP Header `x-api-key`。

### 4.3 向量模型配置 (Embedding)
* **用途**：文本向量化，计算语义相似度。
* **供应商**：阿里云模型服务 (`https://ws-36x1urn0k5njxup5.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings`)
* **模型**：`text-embedding-v4`

### 4.4 可观测性配置 (LangSmith)
* **用途**：记录 Agent 执行轨迹、调试中间变量并监控 Token 消耗。
* **配置参数**：`LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_PROJECT` 等。
* **核心机制**：在 `agents/simple_agent.py` 中利用 `@traceable` 装饰器实现函数级埋点。

---

## 5. 常见运维与测试命令

**1. 初始化/更新知识库 (按需执行)**
```bash
export PYTHONWARNINGS="ignore"
export PYTHONPATH=$(pwd)
python3 retriever/sync_vector.py
```

**2. 启动 Agent 在线服务 (后台常驻)**
```bash
export PYTHONWARNINGS="ignore"
export PYTHONPATH=$(pwd)
nohup python3 main.py > agent_server.log 2>&1 &
```

**3. 客户端发起测试请求 (推荐：流式打字机响应)**
```bash
# 推荐使用提供的流式测试脚本，享受极速打字机体验
python3 test_stream.py "我想知道胶囊是谁负责"
```

**4. 旧版同步阻塞测试 (供参考)**
```powershell
python -c "import requests; print(requests.post('http://10.99.33.39:3000/chat', json={'query': '我想知道胶囊是谁负责'}, headers={'Content-Type': 'application/json; charset=utf-8'}).json()['answer'])"
```

---

## 6. 未来演进与优化方向 (Roadmap)

1. **自动化调度 (Automated Scheduling)**：配合 Linux `crontab` 每天凌晨自动执行增量同步脚本，实现 Token 零损耗与完全自动化。
2. **混合检索 (Hybrid Search)**：在 Milvus 查询时，利用 Metadata 引入标量过滤（如：只检索最近 3 个月的数据，或排除某些离职员工），提高查询精准度。
3. **企业级接入**：将 `0.0.0.0:3000` 接口封装后接入飞书自定义机器人后台，让团队成员可以直接在群聊中 @ 机器人查询业务模块负责人。
