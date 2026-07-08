import json
from langchain_openai import ChatOpenAI
from langsmith import traceable
from schemas.state import AgentState
from prompts.agent_prompts import (
    router_prompt, extract_entity_prompt, synthesize_prompt,
    reflexion_prompt, chitchat_prompt, code_analysis_prompt
)
from retriever.vector_store import get_vector_db
from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

# 初始化 LLM
llm = ChatOpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    model=LLM_MODEL,
    default_headers={"x-api-key": LLM_API_KEY},
    streaming=True  # 明确开启底层 LLM 的流式请求支持
)

@traceable(name="Router_Node")
def router_node(state: AgentState) -> AgentState:
    try:
        chain = router_prompt | llm
        res = chain.invoke({"query": state["query"]})
        intent = res.content.strip().lower()
        if intent not in ["find_owner", "code_analysis", "chitchat"]:
            intent = "chitchat"
        state["intent"] = intent
    except Exception:
        state["intent"] = "chitchat"
    return state

@traceable(name="Extract_Entity_Node")
def extract_entity_node(state: AgentState) -> AgentState:
    try:
        chain = extract_entity_prompt | llm
        res = chain.invoke({"query": state["query"]})
        state["entity"] = res.content.strip()
    except Exception as e:
        state["entity"] = state["query"][:10]
    return state

@traceable(name="Query_Milvus_Node")
def query_db_node(state: AgentState) -> AgentState:
    entity = state["entity"]
    evidence_lines = []
    
    vector_db = get_vector_db()
    if vector_db is None:
        state["evidence"] = "服务端向量数据库初始化失败，请联系管理员。"
        return state
        
    try:
        results = vector_db.similarity_search_with_score(entity, k=10)
        
        dev_counts = {}
        for doc, score in results:
            dev = doc.metadata.get("developer", "Unknown")
            dev_counts[dev] = dev_counts.get(dev, 0) + 1
            
        if not dev_counts:
            state["evidence"] = ""
            return state
            
        for dev, count in dev_counts.items():
            evidence_lines.append(f"开发者: {dev} | 命中精准语义相关代码记录次数: {count}")
            
        state["evidence"] = "\n".join(evidence_lines)
    except Exception as e:
        state["evidence"] = f"Milvus 查询出错: {e}"
        
    return state

@traceable(name="Reflexion_Node")
def reflexion_node(state: AgentState) -> AgentState:
    try:
        chain = reflexion_prompt | llm
        res = chain.invoke({"query": state["query"], "entity": state["entity"]})
        new_keywords = res.content.strip()
        state["entity"] = f"{state['entity']} {new_keywords}"
    except Exception:
        pass
    state["retry_count"] += 1
    return state

@traceable(name="Synthesize_Answer_Node")
def synthesize_node(state: AgentState) -> AgentState:
    try:
        chain = synthesize_prompt | llm
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
    def __init__(self):
        self.max_retries = 1  # 反思重试最大次数

    @traceable(name="Agent_Invoke_Workflow")
    def invoke(self, inputs: dict) -> dict:
        state = AgentState(**inputs, intent="", entity="", evidence="", answer="", retry_count=0)
        
        state = router_node(state)
        
        if state["intent"] == "find_owner":
            state = extract_entity_node(state)
            state = query_db_node(state)
            
            # Reflexion 重试机制
            if not state["evidence"] and state["retry_count"] < self.max_retries:
                state = reflexion_node(state)
                state = query_db_node(state)
                
            if not state["evidence"]:
                state["evidence"] = "未找到任何与该模块高度相关的有效代码提交记录。"
                
            state = synthesize_node(state)
            
        elif state["intent"] == "code_analysis":
            try:
                chain = code_analysis_prompt | llm
                res = chain.invoke({"query": state["query"]})
                state["answer"] = res.content
            except:
                state["answer"] = "调用分析链失败。"
                
        else: # chitchat
            try:
                chain = chitchat_prompt | llm
                res = chain.invoke({"query": state["query"]})
                state["answer"] = res.content
            except:
                state["answer"] = "调用聊天链失败。"
                
        return state

    @traceable(name="Agent_Stream_Workflow")
    def stream(self, inputs: dict):
        state = AgentState(**inputs, intent="", entity="", evidence="", answer="", retry_count=0)
        
        # 1. 意图路由
        state = router_node(state)
        
        if state["intent"] == "find_owner":
            state = extract_entity_node(state)
            state = query_db_node(state)
            
            # 反思机制：如果没搜到，进行同义词扩展并重试
            if not state["evidence"] and state["retry_count"] < self.max_retries:
                yield f"data: {json.dumps({'type': 'status', 'content': '未找到证据，触发 Reflexion 反思，正在扩展关键词重新检索...'}, ensure_ascii=False)}\n\n"
                state = reflexion_node(state)
                # 在 Python 3.8 中，f-string 内部不能使用反斜杠转义引号
                ext_entity = state["entity"]
                status_msg = f"扩展关键词: {ext_entity}"
                yield f"data: {json.dumps({'type': 'status', 'content': status_msg}, ensure_ascii=False)}\n\n"
                state = query_db_node(state)

            if not state["evidence"]:
                state["evidence"] = "经过反思和同义词扩展，依然未找到记录。"
            
            meta_data = {
                "type": "meta",
                "intent": state["intent"],
                "entity_extracted": state["entity"],
                "evidence": state["evidence"]
            }
            yield f"data: {json.dumps(meta_data, ensure_ascii=False)}\n\n"

            try:
                chain = synthesize_prompt | llm
                for chunk in chain.stream({
                    "query": state["query"], 
                    "entity": state["entity"],
                    "evidence": state["evidence"]
                }):
                    if chunk.content:
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.content}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': f'流式生成失败: {e}'}, ensure_ascii=False)}\n\n"

        elif state["intent"] == "code_analysis":
            yield f"data: {json.dumps({'type': 'status', 'content': '意图: 代码分析 | 正在调用代码分析链...'}, ensure_ascii=False)}\n\n"
            try:
                chain = code_analysis_prompt | llm
                for chunk in chain.stream({"query": state["query"]}):
                    if chunk.content:
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.content}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': f'流式生成失败: {e}'}, ensure_ascii=False)}\n\n"

        else: # chitchat
            yield f"data: {json.dumps({'type': 'status', 'content': '意图: 通用问答 (闲聊) | 正在生成回复...'}, ensure_ascii=False)}\n\n"
            try:
                chain = chitchat_prompt | llm
                for chunk in chain.stream({"query": state["query"]}):
                    if chunk.content:
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.content}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': f'流式生成失败: {e}'}, ensure_ascii=False)}\n\n"

        # 传输结束标志
        yield "data: [DONE]\n\n"
