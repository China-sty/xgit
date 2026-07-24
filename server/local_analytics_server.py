# -*- coding: utf-8 -*-
import argparse
import sqlite3
import json
import os
import csv
import re
import logging
import threading
import urllib.request
import urllib.error
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    _d = os.path.dirname(os.path.abspath(__file__))
    _e = os.path.join(_d, 'agent_service', '.env')
    if os.path.exists(_e): load_dotenv(_e)
except ImportError: pass
from collections import defaultdict
from typing import Dict, List, Set, Optional, Tuple

try:
    from flask import Flask, request, jsonify
except ModuleNotFoundError:
    Flask = None
    request = None
    jsonify = None

# ==================== 日志配置 ====================

LOG_PATH = 'server.log'

# 创建自定义的立即刷新Handler
class FlushFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

# 配置日志格式
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# 添加handlers到我们的logger
file_handler = FlushFileHandler(LOG_PATH, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
stream_handler = FlushStreamHandler()
stream_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

app = Flask(__name__) if Flask is not None else None

# 数据库文件路径
DB_PATH = 'git_ai_local_analytics.db'
CSV_PATH = 'ai_penetration_stats.csv'
PERSONNEL_CSV_PATH = '座舱平台研发部人员明细-0415_数据表.csv'
BLACKLIST_CSV_PATH = 'member_blacklist.csv'  # 成员黑名单
COMMIT_BLACKLIST_CSV_PATH = 'commit_blacklist.csv'  # 提交黑名单
FEISHU_WEBHOOK_URL = os.environ.get(
    "FEISHU_WEBHOOK_URL",
    "https://open.feishu.cn/open-apis/bot/v2/hook/aae05e7f-7863-4b7f-9df9-efb403cffdd3",
)

# 云端转发配置（支持多环境）
FORWARD_TARGETS = []

# 测试环境
_test_url = os.environ.get("FORWARD_TEST_URL", "http://logan-gateway.test.logan.xiaopeng.local/xp-ai-coding")
_test_key = os.environ.get("FORWARD_TEST_API_KEY", "xp-ai-coding-2026-api-key")
if _test_url:
    FORWARD_TARGETS.append({"name": "test", "url": _test_url, "api_key": _test_key})

# 生产环境
_prod_url = os.environ.get("FORWARD_PROD_URL", "https://gic-ai-center.xiaopeng.com")
_prod_key = os.environ.get("FORWARD_PROD_API_KEY", "xp-ai-coding-2026-api-key")
if _prod_url:
    FORWARD_TARGETS.append({"name": "prod", "url": _prod_url, "api_key": _prod_key})

# 兼容旧配置
_legacy_url = os.environ.get("FORWARD_TARGET_URL", "")
_legacy_key = os.environ.get("FORWARD_API_KEY", "")
if _legacy_url and not FORWARD_TARGETS:
    FORWARD_TARGETS.append({"name": "default", "url": _legacy_url, "api_key": _legacy_key})

FORWARD_TIMEOUT = 10


def _async_forward(endpoint: str, payload: dict):
    """异步转发请求到云端（fire-and-forget，支持多环境）"""
    if not FORWARD_TARGETS:
        return

    def _do_forward(target):
        url = f"{target['url'].rstrip('/')}{endpoint}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if target.get("api_key"):
            headers["X-Api-Key"] = target["api_key"]
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=FORWARD_TIMEOUT) as resp:
                logger.info(f"[Forward-{target['name']}] {endpoint} -> {url} status={resp.status}")
        except Exception as e:
            logger.warning(f"[Forward-{target['name']}] {endpoint} -> {url} failed: {e}")

    for t in FORWARD_TARGETS:
        threading.Thread(target=_do_forward, args=(t,), daemon=True).start()


_AUTHOR_WITH_EMAIL_RE = re.compile(r'^\s*(?P<name>.*?)\s*<[^>]+>\s*$')
_EMAIL_ONLY_RE = re.compile(r'^\s*(?P<local>[^@\s<>]+)@[^@\s<>]+\s*$')


# ==================== 黑名单管理模块 ====================

class BlacklistManager:
    """成员黑名单管理类"""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.blacklist: Dict[str, dict] = {}  # 邮箱前缀 -> {name, reason}
        self._load()

    def _load(self):
        """从CSV加载黑名单"""
        if not os.path.exists(self.csv_path):
            return
        try:
            with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)  # 跳过表头
                for row in reader:
                    if row and row[0].strip():
                        prefix = row[0].strip().lower()
                        self.blacklist[prefix] = {
                            "name": row[1] if len(row) > 1 else "",
                            "reason": row[2] if len(row) > 2 else ""
                        }
            logger.info(f"[Blacklist] 加载黑名单成功，共 {len(self.blacklist)} 条")
        except Exception as e:
            logger.error(f"[Blacklist] 加载黑名单失败: {e}")

    def _save(self):
        """保存黑名单到CSV"""
        try:
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["email_prefix", "name", "reason"])
                for prefix in sorted(self.blacklist.keys()):
                    info = self.blacklist[prefix]
                    writer.writerow([prefix, info.get("name", ""), info.get("reason", "")])
            logger.info(f"[Blacklist] 保存黑名单成功，共 {len(self.blacklist)} 条")
        except Exception as e:
            logger.error(f"[Blacklist] 保存黑名单失败: {e}")

    def add(self, email_prefix: str, name: str = "", reason: str = "") -> bool:
        """添加到黑名单"""
        prefix = email_prefix.strip().lower()
        if not prefix:
            return False
        if prefix in self.blacklist:
            return False  # 已存在

        self.blacklist[prefix] = {
            "name": name,
            "reason": reason
        }
        # 追加写入CSV
        try:
            file_exists = os.path.exists(self.csv_path)
            with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["email_prefix", "name", "reason"])
                writer.writerow([prefix, name, reason])
            logger.info(f"[Blacklist] 添加黑名单: {prefix} ({name}), 原因: {reason}")
            return True
        except Exception as e:
            logger.error(f"[Blacklist] 添加黑名单失败: {e}")
            return False

    def remove(self, email_prefix: str) -> bool:
        """从黑名单移除"""
        prefix = email_prefix.strip().lower()
        if prefix not in self.blacklist:
            return False
        del self.blacklist[prefix]
        self._save()  # 重写整个文件
        logger.info(f"[Blacklist] 移除黑名单: {prefix}")
        return True

    def is_blacklisted(self, email_prefix: str) -> bool:
        """检查是否在黑名单中"""
        return email_prefix.strip().lower() in self.blacklist

    def get_all(self) -> List[str]:
        """获取所有黑名单（仅邮箱前缀）"""
        return sorted(list(self.blacklist.keys()))

    def get_all_with_details(self) -> List[dict]:
        """获取所有黑名单"""
        result = []
        for prefix in sorted(self.blacklist.keys()):
            info = self.blacklist[prefix]
            result.append({
                "emailPrefix": prefix,
                "name": info.get("name", ""),
                "reason": info.get("reason", "")
            })
        return result


# 全局黑名单实例
_blacklist_manager: Optional[BlacklistManager] = None


def get_blacklist_manager() -> BlacklistManager:
    global _blacklist_manager
    if _blacklist_manager is None:
        _blacklist_manager = BlacklistManager(BLACKLIST_CSV_PATH)
    return _blacklist_manager


class CommitBlacklistManager:
    """提交黑名单管理类"""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.blacklist: Set[str] = set()
        self._load()

    def _load(self):
        """从CSV加载提交黑名单"""
        if not os.path.exists(self.csv_path):
            return
        try:
            with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)  # 跳过表头
                for row in reader:
                    if row and row[0].strip():
                        self.blacklist.add(row[0].strip().lower())
            logger.info(f"[CommitBlacklist] 加载提交黑名单成功，共 {len(self.blacklist)} 条")
        except Exception as e:
            logger.error(f"[CommitBlacklist] 加载提交黑名单失败: {e}")

    def add(self, commit_sha: str) -> str:
        """添加到提交黑名单"""
        sha = commit_sha.strip().lower()
        if not sha:
            return "Invalid commit sha"
        if sha in self.blacklist:
            return "Data duplication"

        self.blacklist.add(sha)
        try:
            file_exists = os.path.exists(self.csv_path)
            with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["commit_sha"])
                writer.writerow([sha])
            logger.info(f"[CommitBlacklist] 添加提交黑名单: {sha}")
            return "添加成功"
        except Exception as e:
            logger.error(f"[CommitBlacklist] 添加提交黑名单失败: {e}")
            return f"Error: {e}"

    def is_blacklisted(self, commit_sha: str) -> bool:
        """检查是否在提交黑名单中"""
        return commit_sha.strip().lower() in self.blacklist

    def remove(self, commit_sha: str) -> bool:
        """从提交黑名单中移除"""
        sha = commit_sha.strip().lower()
        if sha not in self.blacklist:
            return False
        
        self.blacklist.remove(sha)
        # 重写整个CSV文件
        try:
            with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["commit_sha"])
                for s in sorted(self.blacklist):
                    writer.writerow([s])
            logger.info(f"[CommitBlacklist] 移除提交黑名单成功: {sha}")
            return True
        except Exception as e:
            logger.error(f"[CommitBlacklist] 移除提交黑名单失败: {e}")
            return False


# 全局提交黑名单实例
_commit_blacklist_manager: Optional[CommitBlacklistManager] = None


def get_commit_blacklist_manager() -> CommitBlacklistManager:
    global _commit_blacklist_manager
    if _commit_blacklist_manager is None:
        _commit_blacklist_manager = CommitBlacklistManager(COMMIT_BLACKLIST_CSV_PATH)
    return _commit_blacklist_manager


# ==================== 组织管理模块 ====================

class OrganizationTree:
    """组织树管理类"""

    ROOT_ID = "root"
    ROOT_NAME = "座舱平台研发部"

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        # 大部门: {dept_id: {"name": xxx, "members": set()}}
        self.big_depts: Dict[str, dict] = {}
        # 小部门: {dept_id: {"name": xxx, "parent_id": xxx, "members": set()}}
        self.small_depts: Dict[str, dict] = {}
        # 邮箱前缀 -> Person信息
        self.member_map: Dict[str, dict] = {}
        # 大部门名称 -> ID
        self.big_dept_name_to_id: Dict[str, str] = {}
        # 小部门名称 -> ID (在同一大部门下)
        self.small_dept_key_to_id: Dict[str, str] = {}

        self._load()

    def _read_csv(self) -> List[List[str]]:
        """读取CSV文件，自动检测编码"""
        encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
        for enc in encodings:
            try:
                with open(self.csv_path, "r", encoding=enc, newline="") as f:
                    rows = list(csv.reader(f))
                if rows:
                    return rows
            except UnicodeDecodeError:
                continue
        raise RuntimeError(f"无法解码 CSV：{self.csv_path}")

    def _load(self):
        """从CSV加载组织数据"""
        rows = self._read_csv()
        if not rows:
            return

        header = rows[0]
        data = rows[1:]

        # 找到列索引: 姓名, 姓名.部门(小部门), 邮箱前缀, 大部门
        col_map = {h.strip(): i for i, h in enumerate(header)}
        name_idx = col_map.get("姓名", 0)
        small_dept_idx = col_map.get("姓名.部门", 1)
        email_prefix_idx = col_map.get("邮箱前缀", 2)
        big_dept_idx = col_map.get("大部门", 3)

        big_dept_counter = 0
        small_dept_counter = defaultdict(int)

        for row in data:
            if len(row) < 4:
                continue

            name = row[name_idx].strip()
            small_dept_name = row[small_dept_idx].strip().split(",")[0].strip()  # 处理多部门情况
            email_prefix = row[email_prefix_idx].strip().lower()
            big_dept_name = row[big_dept_idx].strip()

            if not email_prefix or not big_dept_name:
                continue

            # 创建或获取大部门ID
            if big_dept_name not in self.big_dept_name_to_id:
                big_dept_counter += 1
                big_dept_id = f"big_{big_dept_counter}"
                self.big_dept_name_to_id[big_dept_name] = big_dept_id
                self.big_depts[big_dept_id] = {
                    "name": big_dept_name,
                    "members": set()
                }
            big_dept_id = self.big_dept_name_to_id[big_dept_name]

            # 如果小部门名称和大部门名称一样，说明是部门老板，直接归属大部门
            if small_dept_name == big_dept_name:
                # 老板直接归属大部门，不创建小部门
                self.member_map[email_prefix] = {
                    "name": name,
                    "email_prefix": email_prefix,
                    "small_dept_id": None,  # 无小部门
                    "big_dept_id": big_dept_id
                }
                self.big_depts[big_dept_id]["members"].add(email_prefix)
                continue

            # 创建或获取小部门ID
            small_dept_key = f"{big_dept_id}:{small_dept_name}"
            if small_dept_key not in self.small_dept_key_to_id:
                small_dept_counter[big_dept_id] += 1
                small_dept_id = f"small_{big_dept_id.split('_')[1]}_{small_dept_counter[big_dept_id]}"
                self.small_dept_key_to_id[small_dept_key] = small_dept_id
                self.small_depts[small_dept_id] = {
                    "name": small_dept_name,
                    "parent_id": big_dept_id,
                    "members": set()
                }
            small_dept_id = self.small_dept_key_to_id[small_dept_key]

            # 记录成员
            self.member_map[email_prefix] = {
                "name": name,
                "email_prefix": email_prefix,
                "small_dept_id": small_dept_id,
                "big_dept_id": big_dept_id
            }
            self.big_depts[big_dept_id]["members"].add(email_prefix)
            self.small_depts[small_dept_id]["members"].add(email_prefix)

    def get_org(self, org_id: str) -> dict:
        """获取组织节点及其子节点"""
        if org_id == self.ROOT_ID:
            # 返回根节点，子节点是所有大部门（排除全员黑名单的部门）
            children = []
            for dept_id, dept_info in self.big_depts.items():
                # 检查该大部门是否有非黑名单成员
                if len(self.get_members_by_dept(dept_id)) == 0:
                    continue
                children.append({
                    "orgId": dept_id,
                    "name": dept_info["name"]
                })
            return {
                "current": {
                    "orgId": self.ROOT_ID,
                    "name": self.ROOT_NAME
                },
                "path": self.ROOT_NAME,
                "children": children
            }

        if org_id in self.big_depts:
            # 返回大部门，子节点是其下的小部门（排除全员黑名单的小部门）
            dept_info = self.big_depts[org_id]
            children = []
            for small_id, small_info in self.small_depts.items():
                if small_info["parent_id"] == org_id:
                    # 检查该小部门是否有非黑名单成员
                    if len(self.get_members_by_dept(small_id)) == 0:
                        continue
                    children.append({
                        "orgId": small_id,
                        "name": small_info["name"]
                    })
            return {
                "current": {
                    "orgId": org_id,
                    "name": dept_info["name"]
                },
                "path": f"{self.ROOT_NAME}/{dept_info['name']}",
                "children": children
            }

        if org_id in self.small_depts:
            # 返回小部门，子节点是成员列表（排除黑名单）
            small_info = self.small_depts[org_id]
            parent_info = self.big_depts[small_info["parent_id"]]
            blacklist = get_blacklist_manager()
            children = []
            for prefix in small_info["members"]:
                if blacklist.is_blacklisted(prefix):
                    continue  # 跳过黑名单成员
                member = self.member_map.get(prefix, {})
                children.append({
                    "orgId": prefix,
                    "name": member.get("name", prefix)
                })
            return {
                "current": {
                    "orgId": org_id,
                    "name": small_info["name"]
                },
                "path": f"{self.ROOT_NAME}/{parent_info['name']}/{small_info['name']}",
                "children": children
            }

        return {"error": f"未找到组织: {org_id}"}

    def get_children(self, org_id: str) -> List[dict]:
        """获取子部门列表"""
        result = self.get_org(org_id)
        return result.get("children", [])

    def get_members_by_dept(self, dept_id: str) -> Set[str]:
        """获取部门下所有成员的邮箱前缀（排除黑名单）"""
        blacklist = get_blacklist_manager()

        if dept_id == self.ROOT_ID:
            # 返回所有成员（排除黑名单）
            return set(p for p in self.member_map.keys() if not blacklist.is_blacklisted(p))

        if dept_id in self.big_depts:
            return set(p for p in self.big_depts[dept_id]["members"] if not blacklist.is_blacklisted(p))

        if dept_id in self.small_depts:
            return set(p for p in self.small_depts[dept_id]["members"] if not blacklist.is_blacklisted(p))

        return set()

    def get_total_members(self, dept_id: str) -> int:
        """获取部门总人数（排除黑名单）"""
        return len(self.get_members_by_dept(dept_id))


# ==================== 统计服务模块 ====================

class StatsService:
    """统计服务类（SQLite适配版）"""

    def __init__(self, db_path: str, org_tree: OrganizationTree):
        self.db_path = db_path
        self.org_tree = org_tree

    def _parse_timestamp(self, dt_str: str) -> int:
        """将日期字符串转为Unix时间戳"""
        if not dt_str:
            return 0
        # 支持格式：2024-04-01 或 2024-04-01T00:00:00 或 2024-04-01 00:00:00
        for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
            try:
                dt = datetime.strptime(dt_str, fmt)
                return int(dt.timestamp())
            except ValueError:
                continue
        return 0

    def _match_email_prefix(self, author: str, prefixes: Set[str]) -> str:
        """
        检查author是否包含任一邮箱前缀，返回匹配的前缀

        使用包含匹配（而非截取），更灵活：
        - "hanzl1@xiaopeng.com" 包含 "hanzl1" → 匹配
        - "韩兆龙 <hanzl1@xiaopeng.com>" 包含 "hanzl1" → 匹配
        - "HANZL1@xiaopeng.com" 包含 "hanzl1" → 匹配（忽略大小写）
        """
        if not author:
            return ""
        author_lower = author.lower()
        for prefix in prefixes:
            if prefix.lower() in author_lower:
                return prefix
        return ""

    def get_summary(self, dept_id: str, start_dt: str, end_dt: str) -> dict:
        """获取部门或个人汇总统计"""
        # 获取部门成员邮箱前缀集合
        member_prefixes = self.org_tree.get_members_by_dept(dept_id)

        # 如果dept_id不是部门，可能是个人（邮箱前缀）
        if len(member_prefixes) == 0 and dept_id in self.org_tree.member_map:
            member_prefixes = {dept_id}

        total_members = len(member_prefixes)

        start_ts = self._parse_timestamp(start_dt)
        end_ts = self._parse_timestamp(end_dt) or int(datetime.now().timestamp())

        # 统计变量
        ai_accepted_lines = 0
        total_added_lines = 0
        commit_count = 0
        active_members = set()

        commit_blacklist = get_commit_blacklist_manager()

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()

            # 查询时间范围内的所有提交
            sql = '''
                SELECT values_json, attributes_json
                FROM metrics_events
                WHERE event_type = 1
            '''
            params = []

            if start_ts > 0:
                sql += ' AND timestamp >= ?'
                params.append(start_ts)
            if end_ts > 0:
                sql += ' AND timestamp <= ?'
                params.append(end_ts)

            cursor.execute(sql, params)

            # Python层过滤和聚合
            for v_str, a_str in cursor.fetchall():
                try:
                    a_json = json.loads(a_str)
                    author = a_json.get("2", "")
                    commit_sha = a_json.get("3", "")
                    base_commit_sha = a_json.get("4", "")
                    
                    if base_commit_sha == "initial":
                        if commit_sha and not commit_blacklist.is_blacklisted(commit_sha):
                            commit_blacklist.add(commit_sha)
                        continue

                    if commit_sha and commit_blacklist.is_blacklisted(commit_sha):
                        continue

                    # 检查是否属于目标部门（包含匹配）
                    matched_prefix = self._match_email_prefix(author, member_prefixes)
                    if not matched_prefix:
                        continue

                    v_json = json.loads(v_str)

                    # 累计统计
                    ai_list = v_json.get("5", [0])
                    ai_lines = ai_list[0] if ai_list else 0
                    added = v_json.get("2", 0)

                    ai_accepted_lines += ai_lines
                    total_added_lines += added
                    commit_count += 1
                    active_members.add(matched_prefix)

                except (json.JSONDecodeError, KeyError, TypeError):
                    continue

        # 计算AI编码占比
        ai_ratio = 0
        if total_added_lines > 0:
            ai_ratio = round(ai_accepted_lines / total_added_lines * 100)

        return {
            "aiCodingRatio": ai_ratio,
            "aiAcceptedLines": ai_accepted_lines,
            "totalAddedLines": total_added_lines,
            "totalMembers": total_members,
            "aiUsingMembers": len(active_members),
            "commitCount": commit_count
        }

    def get_team_ranking(self, dept_id: str, start_dt: str, end_dt: str) -> list:
        """获取子部门AI编码占比排行"""
        children = self.org_tree.get_children(dept_id)

        results = []
        for child in children:
            stats = self.get_summary(child['orgId'], start_dt, end_dt)
            # 排除所有成员都在黑名单的部门（totalMembers为0）
            if stats['totalMembers'] == 0:
                continue
            results.append({
                "teamId": child['orgId'],
                "teamName": child['name'],
                "aiCodingRatio": stats['aiCodingRatio']
            })

        # 按AI占比降序排序
        results.sort(key=lambda x: x['aiCodingRatio'], reverse=True)
        return results

    def get_team_detail(self, dept_id: str, start_dt: str, end_dt: str,
                        page_num: int = 1, page_size: int = 10) -> dict:
        """获取团队明细数据（分页）"""
        children = self.org_tree.get_children(dept_id)

        all_items = []
        for child in children:
            stats = self.get_summary(child['orgId'], start_dt, end_dt)
            # 排除所有成员都在黑名单的部门（totalMembers为0）
            if stats['totalMembers'] == 0:
                continue
            all_items.append({
                "teamId": child['orgId'],
                "teamName": child['name'],
                "aiCodingRatio": stats['aiCodingRatio'],
                "aiAcceptedLines": stats['aiAcceptedLines'],
                "totalAddedLines": stats['totalAddedLines'],
                "commitCount": stats['commitCount'],
                "memberCount": stats['totalMembers'],
                "activeMemberCount": stats['aiUsingMembers']
            })

        # 按AI占比降序排序
        all_items.sort(key=lambda x: x['aiCodingRatio'], reverse=True)

        # 分页
        total = len(all_items)
        start_idx = (page_num - 1) * page_size
        end_idx = start_idx + page_size
        page_items = all_items[start_idx:end_idx]

        return {
            "list": page_items,
            "total": total,
            "pageNum": page_num,
            "pageSize": page_size
        }

    def get_daily_trend(self, dept_id: str, start_dt: str, end_dt: str) -> list:
        """获取每日趋势数据"""
        # 获取部门成员邮箱前缀集合
        member_prefixes = self.org_tree.get_members_by_dept(dept_id)
        total_members = len(member_prefixes)

        start_ts = self._parse_timestamp(start_dt)
        end_ts = self._parse_timestamp(end_dt) or int(datetime.now().timestamp())

        # 按日期聚合的统计数据
        # {date_str: {"ai_lines": 0, "total_lines": 0, "commits": 0, "members": set()}}
        daily_stats = defaultdict(lambda: {
            "ai_lines": 0,
            "total_lines": 0,
            "commits": 0,
            "members": set()
        })

        commit_blacklist = get_commit_blacklist_manager()

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()

            # 查询时间范围内的所有提交，包含timestamp用于日期分组
            sql = '''
                SELECT timestamp, values_json, attributes_json
                FROM metrics_events
                WHERE event_type = 1
            '''
            params = []

            if start_ts > 0:
                sql += ' AND timestamp >= ?'
                params.append(start_ts)
            if end_ts > 0:
                sql += ' AND timestamp <= ?'
                params.append(end_ts)

            cursor.execute(sql, params)

            # Python层过滤和按日期聚合
            for ts, v_str, a_str in cursor.fetchall():
                try:
                    a_json = json.loads(a_str)
                    author = a_json.get("2", "")
                    commit_sha = a_json.get("3", "")
                    base_commit_sha = a_json.get("4", "")
                    
                    if base_commit_sha == "initial":
                        if commit_sha and not commit_blacklist.is_blacklisted(commit_sha):
                            commit_blacklist.add(commit_sha)
                        continue

                    if commit_sha and commit_blacklist.is_blacklisted(commit_sha):
                        continue

                    # 检查是否属于目标部门（包含匹配）
                    matched_prefix = self._match_email_prefix(author, member_prefixes)
                    if not matched_prefix:
                        continue

                    v_json = json.loads(v_str)

                    # 提取日期 (YYYY-MM-DD)
                    date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

                    # 累计统计
                    ai_list = v_json.get("5", [0])
                    ai_lines = ai_list[0] if ai_list else 0
                    added = v_json.get("2", 0)

                    daily_stats[date_str]["ai_lines"] += ai_lines
                    daily_stats[date_str]["total_lines"] += added
                    daily_stats[date_str]["commits"] += 1
                    daily_stats[date_str]["members"].add(matched_prefix)

                except (json.JSONDecodeError, KeyError, TypeError):
                    continue

        # 构建返回结果，按日期升序排列
        results = []
        for date_str in sorted(daily_stats.keys()):
            stats = daily_stats[date_str]
            ai_ratio = 0
            if stats["total_lines"] > 0:
                ai_ratio = round(stats["ai_lines"] / stats["total_lines"] * 100)

            results.append({
                "date": date_str,
                "aiCodingRatio": ai_ratio,
                "aiAcceptedLines": stats["ai_lines"],
                "totalAddedLines": stats["total_lines"],
                "commitCount": stats["commits"],
                "aiUsingMembers": len(stats["members"]),
                "totalMembers": total_members
            })

        return results


# ==================== 全局服务实例 ====================

# 初始化组织树和统计服务（延迟加载）
_org_tree: Optional[OrganizationTree] = None
_stats_service: Optional[StatsService] = None


def get_org_tree() -> OrganizationTree:
    global _org_tree
    if _org_tree is None:
        _org_tree = OrganizationTree(PERSONNEL_CSV_PATH)
    return _org_tree


def get_stats_service() -> StatsService:
    global _stats_service
    if _stats_service is None:
        _stats_service = StatsService(DB_PATH, get_org_tree())
    return _stats_service


# ==================== 原有功能 ====================

def normalize_developer(raw_developer):
    if raw_developer is None:
        return "unknown_developer"
    if not isinstance(raw_developer, str):
        raw_developer = str(raw_developer)
    dev = raw_developer.strip()
    if not dev:
        return "unknown_developer"

    m = _AUTHOR_WITH_EMAIL_RE.match(dev)
    if m:
        dev = m.group("name").strip()

    if "<" in dev and ">" in dev:
        dev = re.sub(r'\s*<[^>]+>\s*', '', dev).strip()

    m = _EMAIL_ONLY_RE.match(dev)
    if m:
        dev = m.group("local").strip()

    return dev or "unknown_developer"


def send_feishu_webhook_text(text, timeout_s=5):
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        FEISHU_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return e.code, body
    except Exception as e:
        return None, str(e)


def clear_db_file():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def rebuild_csv():
    update_csv_stats()


def update_csv_stats():
    """读取所有指标，按开发者计算 AI 渗透率，并更新 CSV 文件"""
    stats = {}
    commit_blacklist = get_commit_blacklist_manager()

    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT values_json, attributes_json FROM metrics_events WHERE event_type = 1")
            rows = cursor.fetchall()

            for v_str, a_str in rows:
                try:
                    v_json = json.loads(v_str)
                    a_json = json.loads(a_str)
                    
                    commit_sha = a_json.get("3", "")
                    base_commit_sha = a_json.get("4", "")
                    
                    if base_commit_sha == "initial":
                        if commit_sha and not commit_blacklist.is_blacklisted(commit_sha):
                            commit_blacklist.add(commit_sha)
                        continue

                    if commit_sha and commit_blacklist.is_blacklisted(commit_sha):
                        continue

                    # '2' 是开发者名字 (author)
                    developer = normalize_developer(a_json.get("2"))

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
        logger.info(f"[Stats] 已实时更新开发者 AI 渗透率统计至 {CSV_PATH}")

    except Exception as e:
        print(f"[Stats Error] 更新统计 CSV 失败: {e}")
        logger.error(f"[Stats] 更新统计 CSV 失败: {e}", exc_info=True)


SUMMARY_FEISHU_URL = os.environ.get(
    "SUMMARY_FEISHU_URL",
    "https://open.feishu.cn/open-apis/bot/v2/hook/4042172f-0716-4eb8-b703-938d22821f2b",
)


def _generate_push_summary(commit_sha, session_ids, branch, diff_stat,
                           commit_message, author):
    try:
        logger.info(f"[Summary] START {commit_sha[:8]} sessions={len(session_ids)}")
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            cur = conn.cursor(); cas = []
            for sid in session_ids:
                # Query more records ordered by recency; session may have thousands of records
                cur.execute(
                    "SELECT data FROM cas_records WHERE metadata LIKE ? ORDER BY id DESC LIMIT 60",
                    (f'%{sid}%',))
                for row in cur.fetchall():
                    try:
                        d = json.loads(row[0]); t = d.get("type",""); m = d.get("message",{})
                        if t == "user": cas.append(f"[用户]{m.get('content','')[:500]}")
                        elif t == "assistant":
                            for b in (m.get("content") if isinstance(m.get("content"),list) else []):
                                if b.get("text"): cas.append(f"[AI]{b['text'][:500]}"); break
                    except Exception: continue
            # Reverse to chronological order (query was DESC), take last 30 turns
            cas.reverse()
            conv = "\n".join(cas[-30:]) or "(无)"
        prompt = (
            f"你是代码审查助手，生成此git push的摘要JSON(不要用markdown包裹):\n"
            f"Commit: {commit_sha[:8]} | 分支: {branch} | 作者: {author}\n"
            f"提交信息: {commit_message}\n"
            f"=== git diff --stat (这就是代码改动，据此描述) ===\n{diff_stat or '(无)'}\n"
            f"=== AI对话(仅供参考，描述人机交互过程，不要复述代码改动) ===\n{conv[:3000]}\n"
            f'输出JSON(四个字段都要简短):\n'
            f'{{"one_liner":"≤20字概括","changes":"按文件列出改动，如 src/foo.rs:+12-3","why":"为什么要做这个改动",'
            f'"conversation":"开发者问了什么→AI怎么帮的，一句话"}}'
        )
        ak = os.environ.get("SUMMARY_LLM_KEY", "sk-9de9c0de7b8349febffde4bba82e4dbe")
        bu = os.environ.get("SUMMARY_LLM_URL", "https://api.deepseek.com/v1")
        md = os.environ.get("SUMMARY_LLM_MODEL", "deepseek-chat")
        logger.info(f"[Summary] calling {md} url={bu} key={'SET' if ak else 'MISSING'}")
        try:
            from openai import OpenAI
            client = OpenAI(base_url=bu, api_key=ak, timeout=60)
            resp = client.chat.completions.create(
                model=md, messages=[{"role":"user","content":prompt}],
                temperature=0.3, max_tokens=800)
            ct = resp.choices[0].message.content.strip()
        except ImportError:
            logger.error("[Summary] openai lib not installed, falling back to urllib")
            req = urllib.request.Request(
                f"{bu}/chat/completions",
                data=json.dumps({"model":md,"messages":[{"role":"user","content":prompt}],"temperature":0.3,"max_tokens":800}).encode(),
                headers={"Content-Type":"application/json","x-api-key":ak}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                ct = json.loads(r.read())["choices"][0]["message"]["content"].strip()
        if ct.startswith("```"): ct = ct.split("\n",1)[-1]; ct = ct[:-3] if ct.endswith("```") else ct
        s = json.loads(ct)
        logger.info(f"[Summary] AI: {s.get('one_liner','')[:80]}")
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.execute('''INSERT OR REPLACE INTO push_summaries(commit_sha,branch,session_ids,one_liner,conversation_summary,changes_summary,diff_stat,why)
                VALUES(?,?,?,?,?,?,?,?)''', (commit_sha, branch, json.dumps(session_ids),
                s.get("one_liner",""), s.get("conversation",""), s.get("changes",""), diff_stat or "",
                s.get("why","")))
            conn.commit()
        logger.info(f"[Summary] SAVED {commit_sha[:8]}")
        card = {"msg_type":"interactive","card":{"header":{"title":{"content":f"🚀 {s.get('one_liner',commit_sha[:8])}","tag":"plain_text"}},
            "elements":[
            {"tag":"div","text":{"tag":"lark_md","content":f"**分支** {branch} | **作者** {author}\n`{commit_sha[:8]}` {commit_message}"}},
            {"tag":"hr"},
            {"tag":"div","text":{"tag":"lark_md","content":f"**📝 改动**\n{s.get('changes','(无)')}"}},
            {"tag":"hr"},
            {"tag":"div","text":{"tag":"lark_md","content":f"**❓ 原因**\n{s.get('why','(无)')}"}},
            {"tag":"hr"},
            {"tag":"div","text":{"tag":"lark_md","content":f"**💬 对话**\n{s.get('conversation','(无)')}"}},
            ]}}
        cr = urllib.request.Request(SUMMARY_FEISHU_URL, data=json.dumps(card, ensure_ascii=False).encode(),
                                     headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(cr, timeout=10) as r: logger.info(f"[Summary] FEISHU {r.status}")
    except Exception as e:
        logger.error(f"[Summary] FAIL: {e}", exc_info=True)


def init_db():
    """初始化 SQLite 数据库表结构"""
    with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
        # 启用 WAL 模式，提高并发读写性能
        conn.execute('PRAGMA journal_mode=WAL')
        cursor = conn.cursor()

        # push_summaries 表
        cursor.execute('''CREATE TABLE IF NOT EXISTS push_summaries(
            id INTEGER PRIMARY KEY AUTOINCREMENT, commit_sha TEXT UNIQUE NOT NULL,
            branch TEXT, session_ids TEXT, one_liner TEXT,
            conversation_summary TEXT, changes_summary TEXT, diff_stat TEXT,
            why TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        # migrate: add why column if missing
        try: cursor.execute("ALTER TABLE push_summaries ADD COLUMN why TEXT")
        except: pass

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

        # 添加索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics_events(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_event_type ON metrics_events(event_type)')

        conn.commit()


# ==================== API 路由 ====================

if app is not None:
    # ==================== CAS (Content-Addressable Storage) 路由 ====================

    @app.route('/worker/cas/upload', methods=['POST'])
    def cas_upload():
        """接收 CAS 对象批量上传，匹配 git-ai 客户端 CasUploadRequest 协议"""
        try:
            payload = request.json
            if not payload:
                logger.warning("[CAS] 收到无效JSON请求")
                return jsonify({"error": "Invalid JSON"}), 400

            objects = payload.get('objects', [])
            if not objects:
                logger.warning("[CAS] objects 为空")
                return jsonify({"error": "Missing objects array"}), 400

            results = []
            success_count = 0
            failure_count = 0

            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cursor = conn.cursor()
                for obj in objects:
                    hash_val = obj.get('hash', '')
                    metadata_val = json.dumps(obj.get('metadata', {}))
                    # 客户端字段名为 content，对应数据库的 data 列
                    content_val = obj.get('content')
                    data_val = json.dumps(content_val, ensure_ascii=False) if content_val else ''

                    if not hash_val:
                        results.append({"hash": "", "status": "error", "error": "Missing hash"})
                        failure_count += 1
                        continue

                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO cas_records (hash, metadata, data)
                            VALUES (?, ?, ?)
                        ''', (hash_val, metadata_val, data_val))
                        results.append({"hash": hash_val, "status": "ok"})
                        success_count += 1
                        logger.info(f"[CAS] 收到提示词数据: {hash_val}")
                    except Exception as e:
                        results.append({"hash": hash_val, "status": "error", "error": str(e)})
                        failure_count += 1
                        logger.error(f"[CAS] 存储失败 {hash_val}: {e}")

                conn.commit()

            logger.info(f"[CAS] 批量上传完成: success={success_count}, failure={failure_count}")
            if success_count > 0:
                text = f"已收到 {success_count} 条CAS提示词数据"
                status, body = send_feishu_webhook_text(text)
                if status is None:
                    logger.error(f"[Feishu] CAS通知发送失败: {body}")
                else:
                    logger.info(f"[Feishu] CAS通知已发送({status}): {text}")
            _async_forward('/worker/cas/upload', payload)
            return jsonify({
                "results": results,
                "success_count": success_count,
                "failure_count": failure_count
            }), 200

        except Exception as e:
            logger.error(f"[CAS] Upload Error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route('/worker/cas/', methods=['GET'])
    def cas_read():
        """按 hash 批量读取 CAS 对象"""
        try:
            hashes_param = request.args.get('hashes', '')
            if not hashes_param:
                return jsonify({"error": "Missing hashes parameter"}), 400

            hashes = [h.strip() for h in hashes_param.split(',') if h.strip()]
            # 安全校验：只允许 hex 字符
            for h in hashes:
                if not all(c in '0123456789abcdefABCDEF' for c in h):
                    return jsonify({"error": f"Invalid hash: {h}"}), 400

            results = []
            success_count = 0
            failure_count = 0

            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
                cursor = conn.cursor()
                for h in hashes:
                    cursor.execute(
                        'SELECT data, metadata FROM cas_records WHERE hash = ?', (h,)
                    )
                    row = cursor.fetchone()
                    if row:
                        try:
                            content = json.loads(row[0]) if row[0] else None
                        except (json.JSONDecodeError, TypeError):
                            content = row[0]
                        results.append({
                            "hash": h,
                            "status": "ok",
                            "content": content
                        })
                        success_count += 1
                    else:
                        results.append({
                            "hash": h,
                            "status": "error",
                            "error": "Not found"
                        })
                        failure_count += 1

            if success_count == 0:
                return jsonify({
                    "results": [],
                    "success_count": 0,
                    "failure_count": failure_count
                }), 404

            logger.info(f"[CAS] 读取完成: success={success_count}, failure={failure_count}")
            return jsonify({
                "results": results,
                "success_count": success_count,
                "failure_count": failure_count
            }), 200

        except Exception as e:
            logger.error(f"[CAS] Read Error: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route('/worker/metrics/upload', methods=['POST'])
    def metrics_upload():
        try:
            payload = request.json
            if not payload or 'events' not in payload:
                logger.warning("[Metrics] 收到无效的Metrics Batch请求")
                return jsonify({"error": "Invalid Metrics Batch"}), 400

            events = payload.get('events', [])
            notify_developers = []

            with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
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
                            logger.info(f"[Metrics] 收到作者: {author}")
                        if e_type == 1 and author:
                            notify_developers.append(normalize_developer(author))
                    except Exception:
                        pass
                conn.commit()

            for event in events:
                if event.get('e', 0) == 8:
                    try:
                        v = event.get('v', {})
                        sha = v.get("0", ""); sids = list(v.get("1", []))
                        br = v.get("2", ""); ds = v.get("3", "")
                        cm = v.get("4", ""); au = v.get("5", "")
                        logger.info(f"[CommitLink] DETECTED sha={sha[:8]} sids={len(sids)}")
                        if sha and sids:
                            threading.Thread(target=_generate_push_summary,
                                             args=(sha, sids, br, ds, cm, au), daemon=True).start()
                    except Exception as e:
                        logger.error(f"[CommitLink] ERR: {e}", exc_info=True)

            for developer in notify_developers:
                text = f"已收到{developer}提交"
                status, body = send_feishu_webhook_text(text)
                if status is None:
                    logger.error(f"[Feishu] 发送失败: {body}")
                else:
                    logger.info(f"[Feishu] 已发送({status}): {text}")

            logger.info(f"[Metrics] 成功接收 {len(events)} 条效能事件")
            _async_forward('/worker/data/report', payload)
            return jsonify({"errors": []}), 200

        except Exception as e:
            logger.error(f"[Metrics] Upload Error: {e}", exc_info=True)
            return jsonify({"error": "Internal Server Error", "details": str(e)}), 500

    # ==================== 新增API路由 ====================

    @app.route('/api/organizations', methods=['GET'])
    def get_organizations():
        """组织层级查询"""
        org_id = request.args.get('orgId', 'root')
        logger.info(f"[API] GET /api/organizations?orgId={org_id}")
        try:
            org_tree = get_org_tree()
            result = org_tree.get_org(org_id)
            logger.info(f"[API] 组织查询成功: orgId={org_id}, children={len(result.get('children', []))}")
            return jsonify(result), 200
        except Exception as e:
            logger.error(f"[API] 组织查询失败: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 400

    @app.route('/api/stats/summary', methods=['GET'])
    def get_stats_summary():
        """部门级汇总"""
        dept_id = request.args.get('deptId', 'root')
        start_dt = request.args.get('startDateTime', '')
        end_dt = request.args.get('endDateTime', '')

        logger.info(f"[API] GET /api/stats/summary?deptId={dept_id}&startDateTime={start_dt}&endDateTime={end_dt}")
        try:
            stats_service = get_stats_service()
            data = stats_service.get_summary(dept_id, start_dt, end_dt)
            logger.info(f"[API] 汇总查询成功: deptId={dept_id}, commitCount={data['commitCount']}, aiRatio={data['aiCodingRatio']}%")
            return jsonify({"code": 0, "data": data}), 200
        except Exception as e:
            logger.error(f"[API] 汇总查询失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 400

    @app.route('/api/stats/team-ranking', methods=['GET'])
    def get_team_ranking():
        """团队AI编码占比排行"""
        dept_id = request.args.get('deptId', 'root')
        start_dt = request.args.get('startDateTime', '')
        end_dt = request.args.get('endDateTime', '')

        logger.info(f"[API] GET /api/stats/team-ranking?deptId={dept_id}&startDateTime={start_dt}&endDateTime={end_dt}")
        try:
            stats_service = get_stats_service()
            data = stats_service.get_team_ranking(dept_id, start_dt, end_dt)
            logger.info(f"[API] 排行查询成功: deptId={dept_id}, teams={len(data)}")
            return jsonify({"code": 0, "data": data}), 200
        except Exception as e:
            logger.error(f"[API] 排行查询失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 400

    @app.route('/api/stats/team-detail', methods=['GET'])
    def get_team_detail():
        """团队明细数据"""
        dept_id = request.args.get('deptId', 'root')
        start_dt = request.args.get('startDateTime', '')
        end_dt = request.args.get('endDateTime', '')
        page_num = int(request.args.get('pageNum', 1))
        page_size = int(request.args.get('pageSize', 10))

        logger.info(f"[API] GET /api/stats/team-detail?deptId={dept_id}&startDateTime={start_dt}&endDateTime={end_dt}&pageNum={page_num}&pageSize={page_size}")
        try:
            stats_service = get_stats_service()
            data = stats_service.get_team_detail(dept_id, start_dt, end_dt, page_num, page_size)
            logger.info(f"[API] 明细查询成功: deptId={dept_id}, total={data['total']}, pageNum={page_num}")
            return jsonify({"code": 0, "data": data}), 200
        except Exception as e:
            logger.error(f"[API] 明细查询失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 400

    @app.route('/api/stats/daily-trend', methods=['GET'])
    def get_daily_trend():
        """每日趋势数据"""
        dept_id = request.args.get('deptId', 'root')
        start_dt = request.args.get('startDateTime', '')
        end_dt = request.args.get('endDateTime', '')

        logger.info(f"[API] GET /api/stats/daily-trend?deptId={dept_id}&startDateTime={start_dt}&endDateTime={end_dt}")
        try:
            stats_service = get_stats_service()
            data = stats_service.get_daily_trend(dept_id, start_dt, end_dt)
            logger.info(f"[API] 每日趋势查询成功: deptId={dept_id}, days={len(data)}")
            return jsonify({"code": 0, "data": data}), 200
        except Exception as e:
            logger.error(f"[API] 每日趋势查询失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 400

    # ==================== 黑名单API ====================

    @app.route('/api/blacklist', methods=['GET'])
    def get_blacklist():
        """查询黑名单列表"""
        logger.info("[API] GET /api/blacklist")
        try:
            blacklist = get_blacklist_manager()
            data = blacklist.get_all_with_details()
            logger.info(f"[API] 黑名单查询成功: count={len(data)}, data: {data}")
            return jsonify({"code": 0, "data": data, "total": len(data)}), 200
        except Exception as e:
            logger.error(f"[API] 黑名单查询失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 400

    @app.route('/api/blacklist', methods=['POST'])
    def add_to_blacklist():
        """添加成员到黑名单"""
        try:
            payload = request.json
            if not payload:
                return jsonify({"code": -1, "error": "Invalid JSON"}), 400

            email_prefix = payload.get('emailPrefix', '').strip()
            reason = payload.get('reason', '').strip()

            if not email_prefix:
                return jsonify({"code": -1, "error": "emailPrefix is required"}), 400

            logger.info(f"[API] POST /api/blacklist emailPrefix={email_prefix}, reason={reason}")

            blacklist = get_blacklist_manager()
            if blacklist.is_blacklisted(email_prefix):
                return jsonify({"code": -1, "error": f"{email_prefix} already in blacklist"}), 400

            # 从org_tree获取姓名
            org_tree = get_org_tree()
            member_info = org_tree.member_map.get(email_prefix.lower(), {})
            name = member_info.get("name", "")

            success = blacklist.add(email_prefix, name, reason)
            if success:
                logger.info(f"[API] 添加黑名单成功: {email_prefix} ({name})")
                return jsonify({"code": 0, "message": f"Added {email_prefix} ({name}) to blacklist"}), 200
            else:
                return jsonify({"code": -1, "error": "Failed to add to blacklist"}), 500
        except Exception as e:
            logger.error(f"[API] 添加黑名单失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 500

    @app.route('/api/blacklist/<email_prefix>', methods=['DELETE'])
    def remove_from_blacklist(email_prefix):
        """从黑名单移除成员"""
        logger.info(f"[API] DELETE /api/blacklist/{email_prefix}")
        try:
            blacklist = get_blacklist_manager()
            if not blacklist.is_blacklisted(email_prefix):
                return jsonify({"code": -1, "error": f"{email_prefix} not in blacklist"}), 404

            success = blacklist.remove(email_prefix)
            if success:
                logger.info(f"[API] 移除黑名单成功: {email_prefix}")
                return jsonify({"code": 0, "message": f"Removed {email_prefix} from blacklist"}), 200
            else:
                return jsonify({"code": -1, "error": "Failed to remove from blacklist"}), 500
        except Exception as e:
            logger.error(f"[API] 移除黑名单失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 500

    # ==================== 提交黑名单API ====================

    @app.route('/api/commit-blacklist', methods=['GET'])
    def get_commit_blacklist():
        """查询提交黑名单列表"""
        logger.info("[API] GET /api/commit-blacklist")
        try:
            blacklist = get_commit_blacklist_manager()
            data = sorted(list(blacklist.blacklist))
            logger.info(f"[API] 提交黑名单查询成功: count={len(data)}")
            return jsonify({"code": 0, "data": data, "total": len(data)}), 200
        except Exception as e:
            logger.error(f"[API] 提交黑名单查询失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 400

    @app.route('/api/commit-blacklist', methods=['POST'])
    def add_to_commit_blacklist():
        """添加提交到黑名单"""
        try:
            payload = request.json
            if not payload:
                return jsonify({"code": -1, "error": "Invalid JSON"}), 400

            commit_sha = payload.get('commitSha', '').strip()

            if not commit_sha:
                return jsonify({"code": -1, "error": "commitSha is required"}), 400

            logger.info(f"[API] POST /api/commit-blacklist commitSha={commit_sha}")

            blacklist = get_commit_blacklist_manager()
            res = blacklist.add(commit_sha)
            
            if res == "Data duplication":
                return jsonify({"code": -1, "error": f"{commit_sha} already in commit blacklist"}), 400
            elif res == "添加成功":
                logger.info(f"[API] 添加提交黑名单成功: {commit_sha}")
                return jsonify({"code": 0, "message": f"Added {commit_sha} to commit blacklist"}), 200
            else:
                return jsonify({"code": -1, "error": res}), 500
        except Exception as e:
            logger.error(f"[API] 添加提交黑名单失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 500


    @app.route('/api/commit-blacklist/<commit_sha>', methods=['DELETE'])
    def remove_from_commit_blacklist(commit_sha):
        """从提交黑名单移除"""
        logger.info(f"[API] DELETE /api/commit-blacklist/{commit_sha}")
        try:
            blacklist = get_commit_blacklist_manager()
            if not blacklist.is_blacklisted(commit_sha):
                return jsonify({"code": -1, "error": f"{commit_sha} not in commit blacklist"}), 404

            res = blacklist.remove(commit_sha)
            if res:
                logger.info(f"[API] 移除提交黑名单成功: {commit_sha}")
                return jsonify({"code": 0, "message": f"Removed {commit_sha} from commit blacklist"}), 200
            else:
                return jsonify({"code": -1, "error": "Failed to remove from commit blacklist"}), 500
        except Exception as e:
            logger.error(f"[API] 移除提交黑名单失败: {e}", exc_info=True)
            return jsonify({"code": -1, "error": str(e)}), 500


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog="local_analytics_server")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("clear-db")
    sub.add_parser("rebuild-csv")
    sub.add_parser("reset")
    sub.add_parser("test")  # 新增测试命令
    
    parser_add_commit_bl = sub.add_parser("add-commit-blacklist")
    parser_add_commit_bl.add_argument("commit_sha", type=str, help="Commit SHA to blacklist")
    
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
    elif cmd == "test":
        # 测试组织树和统计服务
        print("=== 测试组织树 ===")
        org_tree = get_org_tree()
        print(f"大部门数量: {len(org_tree.big_depts)}")
        print(f"小部门数量: {len(org_tree.small_depts)}")
        print(f"成员数量: {len(org_tree.member_map)}")

        print("\n=== 根部门 ===")
        root = org_tree.get_org("root")
        print(json.dumps(root, ensure_ascii=False, indent=2))

        if org_tree.big_depts:
            first_big_id = list(org_tree.big_depts.keys())[0]
            print(f"\n=== 第一个大部门 ({first_big_id}) ===")
            big_dept = org_tree.get_org(first_big_id)
            print(json.dumps(big_dept, ensure_ascii=False, indent=2))

        print("\n=== 测试统计服务 ===")
        stats_service = get_stats_service()
        summary = stats_service.get_summary("root", "", "")
        print(f"根部门汇总: {json.dumps(summary, ensure_ascii=False, indent=2)}")

        print("\n=== 团队排行 ===")
        ranking = stats_service.get_team_ranking("root", "", "")
        print(json.dumps(ranking, ensure_ascii=False, indent=2))
    elif cmd == "add-commit-blacklist":
        sha = args.commit_sha
        manager = get_commit_blacklist_manager()
        res = manager.add(sha)
        print(res)
    else:
        if app is None:
            raise SystemExit("Flask 未安装：请先安装 flask 后再运行 serve 模式")
        logger.info("初始化 SQLite 数据库...")
        print("初始化 SQLite 数据库...")
        init_db()
        logger.info(f"数据库就绪: {os.path.abspath(DB_PATH)}")
        print(f"数据库就绪: {os.path.abspath(DB_PATH)}")
        logger.info(f"日志文件: {os.path.abspath(LOG_PATH)}")
        print(f"日志文件: {os.path.abspath(LOG_PATH)}")
        logger.info("启动本地分析服务器 (监听 5000 端口)...")
        print("启动本地分析服务器 (监听 5000 端口)...")
        print("黑名单配置文件: ", os.path.abspath(BLACKLIST_CSV_PATH))

        # 检测端口是否被占用
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 5000))
        sock.close()
        if result == 0:
            print("❌ 错误: 端口 5000 已被占用，请先停止占用该端口的进程")
            logger.error("端口 5000 已被占用")
            sys.exit(1)

        # 让Flask使用我们的日志handler
        app.logger.handlers = logger.handlers
        app.logger.setLevel(logging.INFO)
        app.run(host='0.0.0.0', port=5000, use_reloader=False)
