import os
import warnings
from dotenv import load_dotenv

# 屏蔽烦人的 PyMilvus 弃用警告
warnings.filterwarnings("ignore", message=".*ORM-style PyMilvus API.*")
warnings.filterwarnings("ignore", module="langchain_milvus")

# 项目根目录 (D:\xgit\agent_service 或 Y:\acsp\agent_service)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 加载 .env 文件中的环境变量
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

# LangSmith 可观测性配置
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGCHAIN_TRACING_V2", "true")
os.environ["LANGCHAIN_ENDPOINT"] = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")  
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "Git_AI_Agent")

# 阿里云 Embedding 配置
ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY", "")
ALIYUN_BASE_URL = os.getenv("ALIYUN_BASE_URL", "https://ws-36x1urn0k5njxup5.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")

# 扶摇网关大模型配置
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://fuyao-ai-gateway.xiaopeng.link/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "kimi-k2.6")

# 知识库与数据源路径配置
_parent_db = os.path.abspath(os.path.join(BASE_DIR, "..", "git_ai_local_analytics.db"))
_default_source_db = _parent_db if os.path.exists(_parent_db) else os.path.join(BASE_DIR, "git_ai_local_analytics.db")

SOURCE_DB_PATH = os.getenv("SOURCE_DB_PATH", _default_source_db)
TEMP_DB_PATH = os.getenv("TEMP_DB_PATH", os.path.join(BASE_DIR, "temp_analytics_readonly.db"))
SYNC_TIME_FILE = os.getenv("SYNC_TIME_FILE", os.path.join(BASE_DIR, "last_sync_time.txt"))

# 同步与索引配置
# 单次增量同步的最大提取条数（避免 OOM 和大量 Token 消耗），可通过环境变量覆盖
SYNC_BATCH_LIMIT = int(os.getenv("SYNC_BATCH_LIMIT", 500))

# Milvus 知识库配置
MILVUS_URI = os.getenv("MILVUS_URI", os.path.join(BASE_DIR, "milvus_demo.db"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "git_ai_knowledge")
