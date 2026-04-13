# -*- coding: utf-8 -*-
import argparse
import sqlite3
import json
import os
import csv
from datetime import datetime

try:
    from flask import Flask, request, jsonify
except ModuleNotFoundError:
    Flask = None
    request = None
    jsonify = None

app = Flask(__name__) if Flask is not None else None

# 数据库文件路径
DB_PATH = 'git_ai_local_analytics.db'
CSV_PATH = 'ai_penetration_stats.csv'

def clear_db_file():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

def rebuild_csv():
    update_csv_stats()

def update_csv_stats():
    """读取所有指标，按开发者计算 AI 渗透率，并更新 CSV 文件"""
    stats = {}
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT values_json, attributes_json FROM metrics_events WHERE event_type = 1")
            rows = cursor.fetchall()
            
            for v_str, a_str in rows:
                try:
                    v_json = json.loads(v_str)
                    a_json = json.loads(a_str)
                    
                    # '2' 是开发者名字 (author)
                    developer = a_json.get("2", "unknown_developer")
                    
                    human_additions = v_json.get("0", 0)
                    ai_additions_list = v_json.get("5", [0])
                    # 取 index 0 的值，它是所有工具/模型的汇总(all)，避免直接 sum() 把 index 1 里的重复统计再加一遍
                    ai_additions = ai_additions_list[0] if ai_additions_list else 0
                    total_added = v_json.get("2", 0)
                    
                    if developer not in stats:
                        stats[developer] = {
                            "total_commits": 0,
                            "total_human_lines": 0,
                            "total_ai_lines": 0,
                            "total_added_lines": 0
                        }
                        
                    stats[developer]["total_commits"] += 1
                    stats[developer]["total_human_lines"] += human_additions
                    stats[developer]["total_ai_lines"] += ai_additions
                    stats[developer]["total_added_lines"] += total_added
                    
                except Exception as parse_e:
                    continue
                    
        # 写入 CSV 文件
        with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['Developer', 'Total Commits', 'Human Lines', 'AI Lines', 'Total Added Lines', 'AI Penetration Rate (%)'])
            
            for dev, data in stats.items():
                total = data["total_added_lines"]
                ai_lines = data["total_ai_lines"]
                rate = (ai_lines / total * 100) if total > 0 else 0.0
                
                writer.writerow([
                    dev, 
                    data["total_commits"], 
                    data["total_human_lines"], 
                    ai_lines, 
                    total, 
                    f"{rate:.2f}"
                ])
                
        print(f"[Stats] 已实时更新开发者 AI 渗透率统计至 {CSV_PATH}")
        
    except Exception as e:
        print(f"[Stats Error] 更新统计 CSV 失败: {e}")

def init_db():
    """初始化 SQLite 数据库表结构"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # CAS (Prompt) 数据表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cas_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT UNIQUE NOT NULL,
                metadata TEXT,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Metrics 事件数据表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metrics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                event_type INTEGER,
                values_json TEXT,
                attributes_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

if app is not None:
    @app.route('/cas/upload', methods=['POST'])
    def cas_upload():
        try:
            payload = request.json
            if not payload:
                return jsonify({"error": "Invalid JSON"}), 400

            hash_val = payload.get('hash')
            metadata_val = json.dumps(payload.get('metadata', {}))
            data_val = payload.get('data', '')

            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO cas_records (hash, metadata, data)
                    VALUES (?, ?, ?)
                ''', (hash_val, metadata_val, data_val))
                conn.commit()

            print(f"[CAS] 收到提示词数据: {hash_val}")
            return jsonify({"message": "success"}), 200

        except Exception as e:
            print(f"CAS Upload Error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/worker/metrics/upload', methods=['POST'])
    def metrics_upload():
        try:
            payload = request.json
            if not payload or 'events' not in payload:
                return jsonify({"error": "Invalid Metrics Batch"}), 400

            events = payload.get('events', [])

            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                for event in events:
                    t = event.get('t', 0)
                    e_type = event.get('e', 0)
                    v_json = json.dumps(event.get('v', {}))
                    a_json = json.dumps(event.get('a', {}))

                    cursor.execute('''
                        INSERT INTO metrics_events (timestamp, event_type, values_json, attributes_json)
                        VALUES (?, ?, ?, ?)
                    ''', (t, e_type, v_json, a_json))
                    try:
                        attrs = event.get('a', {}) or {}
                        author = attrs.get("2")
                        if author:
                            print(f"[Stats] 收到作者: {author}")
                    except Exception:
                        pass
                conn.commit()

            print(f"[Metrics] 成功接收 {len(events)} 条效能事件")
            update_csv_stats()
            return jsonify({"errors": []}), 200

        except Exception as e:
            print(f"Metrics Upload Error: {e}")
            return jsonify({"error": "Internal Server Error", "details": str(e)}), 500

if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog="local_analytics_server")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("clear-db")
    sub.add_parser("rebuild-csv")
    sub.add_parser("reset")
    args = parser.parse_args()

    cmd = args.cmd or "serve"

    if cmd == "clear-db":
        clear_db_file()
        init_db()
        rebuild_csv()
        print("已清空数据库并重新生成 CSV")
    elif cmd == "rebuild-csv":
        init_db()
        rebuild_csv()
        print("已基于当前数据库重新生成 CSV")
    elif cmd == "reset":
        clear_db_file()
        init_db()
        rebuild_csv()
        print("已重置数据库并重新生成 CSV")
    else:
        if app is None:
            raise SystemExit("Flask 未安装：请先安装 flask 后再运行 serve 模式")
        print("初始化 SQLite 数据库...")
        init_db()
        print(f"数据库就绪: {os.path.abspath(DB_PATH)}")
        print("启动本地分析服务器 (监听 5000 端口)...")
        app.run(host='0.0.0.0', port=5000)
