---
name: "cb-agent开发"
description: "用于指导 cb-agent/agent_service 模块的开发规范与架构经验。当用户要求修改、重构或新增 agent_service 相关的任何模块代码时必须触发此技能。"
---

# cb-agent (Agent Service) 开发指南与避坑总结

本项目是一个基于 LangChain 架构、Milvus 向量库和企业级网关大模型搭建的“研发知识中枢” Agent。在后续的开发与维护中，请严格遵循以下架构规范与经验教训。

## 1. 架构规范
项目必须保持标准的模块化 LangChain 目录结构：
- `agents/`: 存放状态机与核心流转节点（如 `simple_agent.py`）。
- `retriever/`: 存放向量数据库连接池与离线清洗同步脚本。
- `config/`: 集中管理配置与环境变量（如 `settings.py`）。
- `prompts/`: 集中管理所有的 Prompt 模板。
- `schemas/`: 定义数据模型（如 Pydantic BaseModel 和 TypedDict）。
- `main.py`: FastAPI 服务入口。

## 2. 核心性能优化（必读）
- **Milvus 知识库连接**：必须在模块级别（全局作用域）进行初始化，作为单例长连接复用。绝对不能在请求处理函数中初始化，否则会导致长达 5 分钟的磁盘 IO 加载与 GRPC 握手延迟甚至 Socket 锁死。
- **流式响应 (Streaming) 与丝滑输出**：
  - 必须在底层 LLM 初始化时显式配置 `streaming=True`。
  - 由于企业网关（如 fuyao-ai-gateway）为了省带宽常常以“大块”返回 Chunk，客户端/调用端在处理 Chunk 时，应通过循环单字符并加入极短的 `time.sleep(0.02)` 来实现人工平滑输出，避免视觉上的“一卡一卡”。

## 3. 离线同步与数据清洗避坑
- **防锁机制**：在执行离线向量化脚本读取 SQLite 时，**必须先使用 `shutil.copy2` 拷贝一份副本作为数据源**，以防与云端写入脚本发生并发读写冲突，导致 `database is locked`。
- **降噪清洗**：绝对禁止将原始埋点 JSON 直接序列化后扔给大模型向量化！必须剔除无意义的 ID、时间戳等噪音数字，仅保留对语义检索有价值的自然语言信息。
- **API 兼容性兜底**：
  - 阿里云 `text-embedding-v4` 不完全兼容原生 OpenAI Embeddings 规范，必须手动通过 `requests` 封装基类。
  - 遇到请求超时或报错时，应当捕获异常并返回 `[0.0] * 1024` 全零向量以保全大局，绝不能中断整个几千条数据的增量同步任务。

## 4. 可观测性与安全
- **全链路追踪**：必须配置 LangSmith。在 `agents/simple_agent.py` 的每一个核心函数（提取、检索、综合推理、总控）上都要加上 `@traceable(name="...")` 装饰器，以便快速排查问题。
- **密钥安全**：绝对禁止在代码中硬编码任何 API Key。必须通过 `os.getenv` 读取，密钥统一配置在 `.env` 文件中，并确保 `.env` 已被加入到项目根目录的 `.gitignore` 中。

## 5. 核心工作流与部署规范（CRITICAL）
- **必须同步远端服务器**：本地环境（如 `D:\xgit\agent_service`）的代码和配置一旦发生修改，**必须立刻将其同步到远端服务器的映射目录**（如 `Y:\acsp\agent_service`），确保远端服务始终运行最新的代码。
- **必须同步更新文档**：任何新增特性、命令变更、配置项修改或架构调整，**必须同步更新 `doc/` 目录下的相关文档**（如 `agent_project_overview.md`、`优化方案.md` 等），保证代码与项目文档始终完全一致。