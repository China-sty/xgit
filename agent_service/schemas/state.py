from typing import TypedDict
from pydantic import BaseModel

class ChatRequest(BaseModel):
    query: str
    stream: bool = False  # 新增 stream 开关，默认为 False 以兼容老接口

class AgentState(TypedDict):
    query: str
    intent: str      # 新增意图分类 (find_owner, code_analysis, chitchat)
    entity: str
    evidence: str
    answer: str
    retry_count: int # 反思重试次数
