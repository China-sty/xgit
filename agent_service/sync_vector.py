import sqlite3
import json
import requests
import os
import shutil
from typing import List
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_milvus import Milvus

# 阿里云配置
ALIYUN_API_KEY = "sk-ws-H.RXRIHRI.ZJGA.MEYCIQCinsQurrHgYIfAsV9Jitikm4UmButE6uPpYSlCQbyTIwIhAO0O729OUzQiOGIfmNvMMHbmkMNK_SVVRTfVaasdJtaG"
ALIYUN_BASE_URL = "https://ws-36x1urn0k5njxup5.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-v4"

class AliyunEmbeddings(Embeddings):
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        headers = {
            "Authorization": f"Bearer {ALIYUN_API_KEY}",
            "Content-Type": "application/json"
        }
        embeddings = []
        for text in texts:
            payload = {
                "model": EMBEDDING_MODEL,
                "input": text
            }
            try:
                resp = requests.post(ALIYUN_BASE_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                embeddings.append(data["data"][0]["embedding"])
            except Exception as e:
                print(f"Embedding API 报错: {e}")
                # 填充0向量防崩溃
                embeddings.append([0.0] * 1024) 
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

def sync_to_milvus():
    print("正在初始化 Aliyun Embeddings...")
    embeddings = AliyunEmbeddings()
    
    docs: List[Document] = []
    
    # 拷贝DB防止文件锁
    db_file = "git_ai_local_analytics.db"
    temp_db = "temp_analytics_readonly.db"
    if os.path.exists(db_file):
        shutil.copy2(db_file, temp_db)
    else:
        print(f"未找到 {db_file} 文件！请确认你在正确的目录下执行。")
        return

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    
    print("开始【清洗】并提取 metrics_events 表数据...")
    try:
        # 只取最近的记录进行演示
        cursor.execute("SELECT created_at, attributes_json, values_json FROM metrics_events ORDER BY created_at DESC LIMIT 2000")
        for row in cursor.fetchall():
            created_at, attr_str, val_str = row
            try:
                attrs = json.loads(attr_str) if attr_str else {}
                vals = json.loads(val_str) if val_str else {}
                
                # 提取开发者，如果提取不到，先不直接丢弃，而是标记为 Unknown
                # 因为在某些埋点数据结构中，开发者信息可能不在 "2" 这个 key 里
                dev = attrs.get("2", "Unknown")
                
                # 核心清洗逻辑：尝试从 values_json 提取任何有价值的文本语义
                content_parts = []
                
                # 1. 尝试提取修改的文件列表
                files = vals.get("files_changed", [])
                if files and isinstance(files, list):
                    content_parts.append(f"修改的文件路径: {', '.join(files)}")
                
                # 2. 尝试提取提交信息
                msg = vals.get("commit_message", "")
                if msg:
                    content_parts.append(f"提交信息: {msg}")
                
                # 3. 如果以上都没有，那就暴力一点，把 values_json 里所有的纯文本字符串值提取出来
                # 这样可以防止过滤条件太严导致一条数据都捞不到
                if not content_parts:
                    for k, v in vals.items():
                        if isinstance(v, str) and len(v) > 5 and not v.startswith("{"):
                            content_parts.append(f"{k}: {v}")

                # 如果想尽办法还是没提取到任何有价值的自然语言，那就真的是纯数字/无意义埋点了，丢弃
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
        URI = "./milvus_demo.db"
        
        Milvus.from_documents(
            docs,
            embeddings,
            connection_args={"uri": URI},
            collection_name="git_ai_knowledge",
            drop_old=True
        )
        print("同步完成！数据已安全、干净地存入 Milvus Lite 中。")
    else:
        print("没有提取到符合条件的数据。")

if __name__ == "__main__":
    sync_to_milvus()