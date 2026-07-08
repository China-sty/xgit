import sqlite3
import json
import os
import sys
import shutil
from typing import List
from tqdm import tqdm

# 确保脚本可以作为独立文件运行或作为模块运行
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document
from langchain_milvus import Milvus

# 导入共用的 Embeddings 类和配置
from retriever.embeddings import AliyunEmbeddings
from config.settings import (
    MILVUS_URI, COLLECTION_NAME, SOURCE_DB_PATH, 
    TEMP_DB_PATH, SYNC_TIME_FILE, SYNC_BATCH_LIMIT
)

def sync_to_milvus():
    print("正在初始化 Aliyun Embeddings...")
    embeddings = AliyunEmbeddings()
    
    docs: List[Document] = []
    
    # 拷贝DB防止文件锁
    # SQLite 在读取时如果其他进程（如云端上传脚本）正在高频写入，容易导致 database is locked 错误。
    # 所以我们在离线同步前，先完整拷贝一份副本来作为只读数据源，牺牲一点磁盘空间换取绝对的并发安全。
    if os.path.exists(SOURCE_DB_PATH):
        print(f"正在读取数据源: {SOURCE_DB_PATH}")
        shutil.copy2(SOURCE_DB_PATH, TEMP_DB_PATH)
    else:
        print(f"未找到 {SOURCE_DB_PATH} 文件！请确认数据源是否存在。")
        return

    conn = sqlite3.connect(TEMP_DB_PATH)
    cursor = conn.cursor()
    
    print(f"开始【清洗】并提取 metrics_events 表数据 (限制 {SYNC_BATCH_LIMIT} 条)...")
    try:
        # 读取上次同步的时间戳
        last_sync_time = "1970-01-01 00:00:00"
        if os.path.exists(SYNC_TIME_FILE):
            with open(SYNC_TIME_FILE, "r", encoding="utf-8") as f:
                last_sync_time = f.read().strip()

        print(f"上次同步时间: {last_sync_time}，开始增量提取...")
        
        # 增量查询，只取大于上次同步时间的记录，按时间正序排列
        query = "SELECT created_at, attributes_json, values_json FROM metrics_events WHERE created_at > ? ORDER BY created_at ASC LIMIT ?"
        cursor.execute(query, (last_sync_time, SYNC_BATCH_LIMIT))
        
        max_created_at = last_sync_time
        for row in cursor.fetchall():
            created_at, attr_str, val_str = row
            
            if created_at > max_created_at:
                max_created_at = created_at
                
            try:
                attrs = json.loads(attr_str) if attr_str else {}
                vals = json.loads(val_str) if val_str else {}
                
                # 提取开发者，如果提取不到，先不直接丢弃，而是标记为 Unknown
                # 这是因为部分早期的埋点可能丢失了元数据，但它的自然语言描述仍然有向量检索的价值。
                dev = attrs.get("2", "Unknown")
                
                # 核心清洗逻辑：尝试从 values_json 提取任何有价值的文本语义
                # 这里不直接把整个 JSON 序列化向量化，因为包含大量如 "timestamp", "id" 等无意义的数字，
                # 这种噪音数据会严重干扰大模型的 Embedding 模型，导致计算出来的向量距离不准确。
                content_parts = []
                
                files = vals.get("files_changed", [])
                if files and isinstance(files, list):
                    content_parts.append(f"修改的文件路径: {', '.join(files)}")
                
                msg = vals.get("commit_message", "")
                if msg:
                    content_parts.append(f"提交信息: {msg}")
                
                if not content_parts:
                    for k, v in vals.items():
                        if isinstance(v, str) and len(v) > 5 and not v.startswith("{"):
                            content_parts.append(f"{k}: {v}")

                if not content_parts:
                    continue
                    
                page_content = " | ".join(content_parts)
                
                metadata = {
                    "source": "metrics_events",
                    "developer": dev,
                    "created_at": created_at,
                    "lines_added": vals.get("lines_added", 0)
                }
                
                docs.append(Document(page_content=page_content, metadata=metadata))
            except Exception as e:
                pass
    except Exception as e:
        print(f"读取 metrics_events 失败: {e}")
        
    print(f"清洗完毕！共提取了 {len(docs)} 条高质量的有效代码提交记录。")
    print("开始调用阿里云 API 进行向量化，并存入 Milvus...")
    
    if len(docs) > 0:
        # 直接使用 settings 中的 MILVUS_URI
        milvus_path = MILVUS_URI.replace("./", "") if MILVUS_URI.startswith("./") else MILVUS_URI
        
        Milvus.from_documents(
            docs,
            embeddings,
            connection_args={"uri": milvus_path},
            collection_name=COLLECTION_NAME,
            drop_old=False
        )
        
        # 更新同步时间戳
        with open(SYNC_TIME_FILE, "w", encoding="utf-8") as f:
            f.write(max_created_at)
            
        print("同步完成！增量数据已安全、干净地存入 Milvus Lite 中。")
    else:
        print("没有提取到符合条件的新数据，无需同步。")

if __name__ == "__main__":
    sync_to_milvus()
