import requests
from typing import List
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_core.embeddings import Embeddings
from config.settings import ALIYUN_API_KEY, ALIYUN_BASE_URL, EMBEDDING_MODEL

class AliyunEmbeddings(Embeddings):
    """
    自定义的阿里云 Embedding 包装类。
    为什么我们要手写这个类而不是用 LangChain 自带的 OpenAIEmbeddings 替换 BaseURL？
    因为阿里云的 text-embedding-v4 API 返回的 JSON 结构与 OpenAI 原生结构不完全兼容，
    直接使用官方包会抛出 "InvalidParameter: contents is neither str nor list of str" 的错误。
    因此我们继承了 Base Embeddings 并通过原生 requests 手动封装请求。
    """
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        headers = {
            "Authorization": f"Bearer {ALIYUN_API_KEY}", 
            "Content-Type": "application/json"
        }
        
        # 批量大小：阿里云通常允许一次传入最多 25 个 input
        batch_size = 20
        # 将整个文本列表切分成若干个 batch
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        
        # 用于存放打平后的所有 embedding 结果，初始用 None 占位
        final_embeddings = [None] * len(texts)
        
        # 定义单个 batch 请求的任务函数
        def fetch_batch(batch_idx, batch_texts):
            try:
                resp = requests.post(
                    ALIYUN_BASE_URL, 
                    json={"model": EMBEDDING_MODEL, "input": batch_texts}, 
                    headers=headers,
                    timeout=15  # 设置超时防止线程卡死
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                # 阿里云返回的 data 数组顺序与 input 一致，直接提取
                return batch_idx, [item["embedding"] for item in data]
            except Exception as e:
                # 批量失败时，给这个 batch 里所有的文本都填充全 0 向量
                return batch_idx, [[0.0] * 1024 for _ in batch_texts]

        # 使用线程池并发请求 (最大开启 10 个线程，防止被网关拦截或封 IP)
        with ThreadPoolExecutor(max_workers=10) as executor:
            # 提交所有任务，记录 futures
            futures = {executor.submit(fetch_batch, i, batch): i for i, batch in enumerate(batches)}
            
            # 使用 tqdm 监控进度，注意这里进度条单位变成了 batch
            for future in tqdm(as_completed(futures), total=len(batches), desc="向量化进度 (并发 Batch)", unit="批次"):
                batch_idx, batch_embeddings = future.result()
                
                # 将该 batch 的结果回填到 final_embeddings 对应的位置
                start_idx = batch_idx * batch_size
                for j, emb in enumerate(batch_embeddings):
                    final_embeddings[start_idx + j] = emb
                    
        return final_embeddings

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]
