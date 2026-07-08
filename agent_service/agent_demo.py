import os
import json
from typing import List, Dict, Any, TypedDict
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_milvus import Milvus
from langchain_core.embeddings import Embeddings
import requests

# 阿里云 Embedding 配置
ALIYUN_API_KEY = "sk-ws-H.RXRIHRI.ZJGA.MEYCIQCinsQurrHgYIfAsV9Jitikm4UmButE6uPpYSlCQbyTIwIhAO0O729OUzQiOGIfmNvMMHbmkMNK_SVVRTfVaasdJtaG"
ALIYUN_BASE_URL = "https://ws-36x1urn0k5njxup5.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-v4"

class AliyunEmbeddings(Embeddings):
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        headers = {"Authorization": f"Bearer {ALIYUN_API_KEY}", "Content-Type": "application/json"}
        embeddings = []
        for text in texts:
            try:
                resp = requests.post(ALIYUN_BASE_URL, json={"model": EMBEDDING_MODEL, "input": text}, headers=headers)
                resp.raise_for_status()
                embeddings.append(resp.json()["data"][0]["embedding"])
            except:
                embeddings.append([0.0] * 1024)
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

# 扶摇网关大模型配置
LLM_API_KEY = "3d2623dffb6c49fca2d990ef4f7c2199"
LLM_BASE_URL = "https://fuyao-ai-gateway.xiaopeng.link/v1"
LLM_MODEL = "kimi-k2.6"

llm = ChatOpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    model=LLM_MODEL,
    default_headers={"x-api-key": LLM_API_KEY}
)

# 【核心性能优化】：将全局对象的初始化移到函数外部，作为单例长连接复用
print("正在预加载并连接 Milvus 知识库...")
try:
    global_embeddings = AliyunEmbeddings()
    # 提前连接好，驻留内存
    global_vector_db = Milvus(
        embedding_function=global_embeddings,
        connection_args={"uri": "./milvus_demo.db"},
        collection_name="git_ai_knowledge"
    )
    print("Milvus 知识库连接成功！")
except Exception as e:
    print(f"Milvus 预加载失败，请检查同步脚本是否跑完: {e}")
    global_vector_db = None


app = FastAPI()

class ChatRequest(BaseModel):
    query: str

class AgentState(TypedDict):
    query: str
    entity: str
    evidence: str
    answer: str

def extract_entity_node(state: AgentState) -> AgentState:
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个系统分析助手。请从用户的提问中提取出核心业务模块名称（如 XAppMode、胶囊、后排屏等）。只输出实体名词本身，不要其他任何字符。"),
        ("user", "{query}")
    ])
    try:
        chain = prompt | llm
        res = chain.invoke({"query": state["query"]})
        state["entity"] = res.content.strip()
    except Exception as e:
        state["entity"] = state["query"][:10]
    return state

def query_db_node(state: AgentState) -> AgentState:
    entity = state["entity"]
    evidence_lines = []
    
    if global_vector_db is None:
        state["evidence"] = "服务端向量数据库初始化失败，请联系管理员。"
        return state
        
    try:
        # 【核心性能优化】：复用全局单例连接，直接查询，告别冷启动延迟
        results = global_vector_db.similarity_search_with_score(entity, k=10)
        
        dev_counts = {}
        for doc, score in results:
            dev = doc.metadata.get("developer", "Unknown")
            dev_counts[dev] = dev_counts.get(dev, 0) + 1
            
        if not dev_counts:
            state["evidence"] = "未找到任何与该模块高度相关的有效代码提交记录。"
            return state
            
        for dev, count in dev_counts.items():
            evidence_lines.append(f"开发者: {dev} | 命中精准语义相关代码记录次数: {count}")
            
        state["evidence"] = "\n".join(evidence_lines)
    except Exception as e:
        state["evidence"] = f"Milvus 查询出错: {e}"
        
    return state

def synthesize_node(state: AgentState) -> AgentState:
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个研发效能分析师。根据后台查询到的客观证据，回答用户的提问。\n"
                   "【严格要求】：如果证据显示未找到记录，请直接明确告诉用户没有数据，绝不允许瞎编或猜测责任人！"),
        ("user", "用户问题: {query}\n\n后台提取模块: {entity}\n\n后台证据:\n{evidence}\n\n请给出分析结论和推荐负责人。")
    ])
    try:
        chain = prompt | llm
        res = chain.invoke({
            "query": state["query"], 
            "entity": state["entity"],
            "evidence": state["evidence"]
        })
        state["answer"] = res.content
    except Exception as e:
        state["answer"] = "分析生成失败。"
    return state

class SimpleAgent:
    def invoke(self, inputs: dict) -> dict:
        state = AgentState(**inputs, entity="", evidence="", answer="")
        state = extract_entity_node(state)
        state = query_db_node(state)
        state = synthesize_node(state)
        return state

agent = SimpleAgent()

@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    result = agent.invoke({"query": req.query})
    return {
        "entity_extracted": result["entity"],
        "evidence": result["evidence"],
        "answer": result["answer"]
    }

if __name__ == "__main__":
    print("Agent Server is starting on 0.0.0.0:3000...")
    uvicorn.run(app, host="0.0.0.0", port=3000)