import asyncio
import aiohttp
import time
import json
import argparse

# 模拟十个不同的并发问题
QUERIES = [
    "我想知道胶囊是谁负责",
    "帮我分析一下这段代码的AST",
    "充放电模块是哪个开发写的？",
    "你好，今天天气怎么样？",
    "后排屏桌面的bug谁负责",
    "我想知道XAppMode的作者是谁",
    "帮我查一下蓝牙模块的代码提交记录",
    "代码仓库的目录结构是怎样的？",
    "OTA升级模块的负责人是谁",
    "我应该怎么配置环境？"
]

async def fetch_stream(session, query, index):
    url = "http://10.99.33.39:3000/chat"
    payload = {"query": query, "stream": True}
    
    start_time = time.time()
    first_byte_time = None
    
    print(f"[Client {index}] 发送请求: {query}")
    
    try:
        async with session.post(url, json=payload, headers={'Content-Type': 'application/json'}) as response:
            if response.status != 200:
                print(f"[Client {index}] ❌ 请求失败，状态码: {response.status}")
                return
                
            full_response = ""
            async for line in response.content:
                if line:
                    if first_byte_time is None:
                        first_byte_time = time.time()
                        
                    decoded_line = line.decode('utf-8').strip()
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            if data_json.get("type") == "chunk":
                                full_response += data_json.get("content", "")
                        except json.JSONDecodeError:
                            pass
                            
            end_time = time.time()
            ttfb = first_byte_time - start_time if first_byte_time else 0
            total_time = end_time - start_time
            
            print(f"[Client {index}] ✅ 完成! (首字节延迟: {ttfb:.2f}s, 总耗时: {total_time:.2f}s) | 最终回复长度: {len(full_response)}字")
            
    except Exception as e:
        print(f"[Client {index}] ❌ 发生异常: {e}")

async def main(concurrency: int):
    print(f"🚀 开始发起 {concurrency} 个并发请求压测...")
    start_time = time.time()
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(concurrency):
            # 循环取问题，保证就算开 100 并发也有词可搜
            query = QUERIES[i % len(QUERIES)]
            task = asyncio.create_task(fetch_stream(session, query, i+1))
            tasks.append(task)
            
        await asyncio.gather(*tasks)
        
    total_time = time.time() - start_time
    print(f"\n🎉 压测结束！总耗时: {total_time:.2f}秒 (平均每秒处理请求: {concurrency/total_time:.2f} QPS)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent API 并发压测工具")
    parser.add_argument("-c", "--concurrency", type=int, default=10, help="并发数量 (默认: 10)")
    args = parser.parse_args()
    
    asyncio.run(main(args.concurrency))
