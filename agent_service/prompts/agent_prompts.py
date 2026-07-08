from langchain_core.prompts import ChatPromptTemplate

router_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个意图识别路由器。根据用户的提问，将其归类为以下三种意图之一，并只输出对应的英文单词：\n"
               "1. find_owner: 寻找某个业务模块、代码的负责人、谁负责等。\n"
               "2. code_analysis: 要求分析代码逻辑、AST分析、读源码等。\n"
               "3. chitchat: 闲聊、通用问答、其他。\n"
               "只输出意图单词，不要多余字符。"),
    ("user", "{query}")
])

extract_entity_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个系统分析助手。请从用户的提问中提取出核心业务模块名称（如 XAppMode、胶囊、后排屏等）。只输出实体名词本身，不要其他任何字符。"),
    ("user", "{query}")
])

reflexion_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个查询扩展助手。之前的检索实体 '{entity}' 没有找到相关记录。\n"
               "请给出该实体的同义词、英文翻译或可能的相关缩写，用空格分隔。只输出扩展后的关键词，不要废话。"),
    ("user", "原问题：{query}\n原实体：{entity}")
])

chitchat_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个友好的AI助手，也是一名研发效能专家，请耐心解答用户的问题。"),
    ("user", "{query}")
])

code_analysis_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个代码分析助手。由于目前系统处于架构演进中，尚未接入真实的 Git AST 分析工具，请礼貌地告知用户当前只能做基础解答，未来会支持深度源码分析。"),
    ("user", "{query}")
])

synthesize_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个研发效能分析师。根据后台查询到的客观证据，回答用户的提问。\n"
               "【严格要求】：如果证据显示未找到记录，请直接明确告诉用户没有数据，绝不允许瞎编或猜测责任人！"),
    ("user", "用户问题: {query}\n\n后台提取模块: {entity}\n\n后台证据:\n{evidence}\n\n请给出分析结论和推荐负责人。")
])
