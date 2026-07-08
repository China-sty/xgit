import requests
import json
import sys
import argparse
import time

def test_stream(query: str):
    url = "http://10.99.33.39:3000/chat"
    payload = {
        "query": query,
        "stream": True
    }
    headers = {"Content-Type": "application/json; charset=utf-8"}

    print(f"发送请求到 {url} (开启流式响应)...")
    try:
        with requests.post(url, json=payload, headers=headers, stream=True) as resp:
            resp.raise_for_status()
            
            for line in resp.iter_lines():
                if line:
                    # 解析 SSE 格式: "data: {...}"
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith("data: "):
                        data_str = decoded_line[6:]
                        
                        if data_str == "[DONE]":
                            print("\n\n✅ 响应结束。")
                            break
                            
                        try:
                            data_json = json.loads(data_str)
                            msg_type = data_json.get("type")
                            
                            # 第一阶段：立刻拿到检索结果
                            if msg_type == "meta":
                                print("\n" + "="*40)
                                print(f"🔍 提取实体: {data_json.get('entity_extracted')}")
                                print(f"📄 检索证据:\n{data_json.get('evidence')}")
                                print("="*40 + "\n")
                                print("🤖 AI 分析结论 (打字机效果): ", end="", flush=True)
                                
                            # 第二阶段：逐字打印大模型思考过程
                            elif msg_type == "chunk":
                                text = data_json.get("content", "")
                                # 【核心丝滑优化】：由于企业网关为了省带宽，往往是一大块一块(10-20字)返回数据，
                                # 直接 print 会导致视觉上一卡一卡。这里加入 20ms 的人工平滑输出。
                                for char in text:
                                    print(char, end="", flush=True)
                                    time.sleep(0.02)
                                
                            # 错误处理
                            elif msg_type == "error":
                                print(data_json.get("content", ""))
                                
                        except json.JSONDecodeError:
                            pass
    except Exception as e:
        print(f"\n请求出错: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测试流式响应")
    parser.add_argument("query", type=str, nargs="?", default="我想知道胶囊是谁负责", help="你想问的问题")
    args = parser.parse_args()
    
    test_stream(args.query)