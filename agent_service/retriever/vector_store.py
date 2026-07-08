from langchain_milvus import Milvus
from retriever.embeddings import AliyunEmbeddings
from config.settings import MILVUS_URI, COLLECTION_NAME

# 【核心性能优化】：将全局对象的初始化移到模块级别，作为单例长连接复用
# 在早期版本中，如果将 Milvus() 的初始化放在每次查询请求中，
# 会导致每次请求都需要进行繁重的磁盘 I/O 加载和 GRPC 通信握手，引发长达 5 分钟的延迟甚至 Socket 锁死。
# 提升为全局变量后，服务启动时加载一次并常驻内存，查询延迟从 5 分钟降至约 300 毫秒。
print("正在预加载并连接 Milvus 知识库...")
try:
    global_embeddings = AliyunEmbeddings()
    # 提前连接好，驻留内存
    global_vector_db = Milvus(
        embedding_function=global_embeddings,
        connection_args={"uri": MILVUS_URI},
        collection_name=COLLECTION_NAME
    )
    print("Milvus 知识库连接成功！")
except Exception as e:
    print(f"Milvus 预加载失败，请检查同步脚本是否跑完: {e}")
    global_vector_db = None

def get_vector_db():
    return global_vector_db
