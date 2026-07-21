# git-ai 开发指南

> xgit (fork of [git-ai-project/git-ai](https://github.com/git-ai-project/git-ai))

## 一、编译

```bash
# 在项目根目录
cargo build --release
# 产物: target/release/git-ai.exe (Windows) / target/release/git-ai (Linux/Mac)
```

编译时间约 1-2 分钟（增量编译更快）。常见 warning（dead_code）可忽略，确保没有 `error` 即可。

## 二、安装

git-ai 是 git 代理，安装需要替换 `~/.git-ai/bin/` 下的二进制文件并重启 daemon。

```bash
# 1. 停止 daemon
git-ai bg shutdown

# 2. 等待进程退出（重要：否则文件被占用）
sleep 2

# 3. 复制二进制
cp target/release/git-ai.exe ~/.git-ai/bin/git-ai.exe   # 主程序
cp target/release/git-ai.exe ~/.git-ai/bin/git.exe       # git 代理

# 4. 启动 daemon
git-ai bg start

# 5. 验证
sleep 2
git-ai --version
```

> **注意**: Windows 上如果 cp 报 `Device or resource busy`，说明 daemon 没完全退出。
> 可以用 `git-ai bg shutdown --hard` 强制停止，等 5 秒再试。

## 三、验证安装

### 3.1 基本功能

```bash
# 版本
git-ai --version

# daemon 状态（last_error 应为 null）
git-ai bg status

# AI 归因统计
git-ai stats HEAD --json
git-ai usage
```

### 3.2 AI 归因测试流程

测试 git-ai 是否正确追踪 AI 代码生成：

```bash
# 1. 确保 daemon 运行且无错误
git-ai bg status | grep last_error   # 应为 null

# 2. 创建测试文件并提交
echo "// AI: test" > src/test_ai.rs
echo "pub fn test() -> bool { true }" >> src/test_ai.rs
git add src/test_ai.rs
git commit -m "test: verify AI attribution"

# 3. 等 daemon 处理（3-5 秒）
sleep 5

# 4. 检查归因
git-ai stats HEAD --json
# 正常输出: {"ai_additions":2, "tool_model_breakdown":{"claude::xxx":{"ai_additions":2}}}
# 异常输出: {"unknown_additions":2, "ai_additions":0}  ← hooks 未激活

# 5. 检查行级归因
git notes --ref=ai show HEAD
# 正常应有: src/test_ai.rs  s_xxx::t_xxx 1-2
```

### 3.3 Git 操作归因保留测试

测试各种 git 操作后 AI 归因是否保留：

```bash
# === Stash 测试 ===
echo "pub fn test2() -> bool { false }" >> src/test_ai.rs
git add src/test_ai.rs
git stash push -m "test: stash"              # stash push

# 验证 stashes_v2 目录有新条目
ls .git/ai/stashes_v2/                        # 应有新 SHA 目录

git stash pop                                  # stash pop
git add src/test_ai.rs
git commit -m "test: after stash"
sleep 5
git-ai stats HEAD --json                      # 检查归因

# === Cherry-pick 测试 ===
git checkout -b test/cp-src
echo "// AI: cp test" > src/test_cp.rs
echo "pub fn cp() -> i32 { 42 }" >> src/test_cp.rs
git add src/test_cp.rs
git commit -m "test: cp source"
CP_SHA=$(git rev-parse HEAD)

git checkout main
git checkout -b test/cp-target
git cherry-pick $CP_SHA
sleep 5
git-ai stats HEAD --json                      # 检查归因

# === Rebase fixup 测试 ===
echo "// AI: rebase base" > src/test_rb.rs
echo "pub fn rb1() -> i32 { 1 }" >> src/test_rb.rs
git add src/test_rb.rs
git commit -m "test: rebase base"

echo "pub fn rb2() -> i32 { 2 }" >> src/test_rb.rs
git add src/test_rb.rs
git commit -m "fixup! test: rebase base"

GIT_SEQUENCE_EDITOR="sed -i '2s/pick/fixup/'" git rebase -i HEAD~2
sleep 5
git-ai stats HEAD --json                      # 检查归因

# === 清理 ===
git checkout main
git branch -D test/cp-src test/cp-target
rm -f src/test_*.rs
```

### 3.4 预期结果

| 操作 | 正常结果 | 异常表现 |
|------|----------|----------|
| 普通 commit | `ai_additions: N` | `unknown_additions: N` |
| stash → pop → commit | `ai_additions: N` | `unknown_additions: N` |
| cherry-pick | 源 commit 的 AI note 被迁移 | 空 note |
| rebase fixup | squash 后归因保留 | 空 note / no note |

**异常的最常见原因**:
1. **Claude Code 未重启** — 安装新版本后 hooks 需要重启 Claude Code
2. **daemon 未运行** — `git-ai bg status` 检查
3. **GitHub 网络不通** — 中国网络环境需开代理

## 四、常见问题

### 4.1 hooks 不生效

```bash
git-ai install-hooks --check
# 如果提示 "agents must be restarted" → 重启 Claude Code
```

### 4.2 daemon 日志

```bash
# 查看最近日志
git-ai bg tail -n 50

# 实时追踪
git-ai bg tail -f

# 搜索特定操作
git-ai bg tail -n 100 | grep -E "stash|commit|error"
```

### 4.3 调试 daemon 事件

```bash
# 开启 debug trace
export GIT_AI_DEBUG_DAEMON_TRACE=1
git-ai bg restart
# 之后操作会在日志中输出 ref_changes、events 等详细信息
```

### 4.4 清理测试数据

```bash
git checkout main
git branch -D test-branch1 test-branch2 ... 2>/dev/null
git stash clear
rm -f src/test_*.rs
# stashes_v2 是 daemon 管理的，重启 daemon 不会自动清理，但不会影响功能
```

### 4.5 网络问题

```bash
# 测试 GitHub 连通性
curl -s --connect-timeout 5 https://github.com -o /dev/null -w "%{http_code}\n"
# 200 = 通, 000 = 不通（需开代理/VPN）

# 配置 git 代理
git config --global http.proxy http://127.0.0.1:7890
git config --global https.proxy http://127.0.0.1:7890
```

## 五、已修复的问题

本 fork 基于 upstream v1.6.16，针对 reflog cursor 在 proxy 模式下的时序问题做了以下修复：

### 5.1 Stash 归因丢失

**根因**: daemon 的 `ref_cursor.enrich_stash()` 在 proxy 模式下读取 reflog 时，git 尚未写入 reflog entry，导致 `stash_target_oid` 为空，`handle_stash_create` 被跳过。

**修复** (`src/daemon.rs`):
- 新增 `stash_sha_from_git()` 函数，直接调用 `git rev-parse --verify refs/stash` 获取 stash SHA
- 在 Push/Pop/Apply/Branch/Drop 事件处理中添加 git fallback
- `worktree` 为 None 时回退到 `current_dir()`

### 5.2 Rebase 归因丢失

**根因**: `rebase_new_tip_from_command` 和 `strict_rebase_original_head_from_command` 仅从 `cmd.ref_changes` 解析，无 git fallback。

**修复** (`src/daemon.rs`):
- 新增 `git_rev_parse(worktree, rev)` 通用函数
- `rebase_new_tip_from_command` 返回 None 时 fallback 到 `git rev-parse HEAD`
- `strict_rebase_original_head_from_command` 返回 None 时 fallback 到 `git rev-parse ORIG_HEAD`

### 5.3 Cherry-pick

cherry-pick 上游已有 `resolve_cherry_pick_single_source_with_git` 作为 git fallback，无需额外修复。

## 六、同步上游

```bash
# 1. 备份
git branch backup/pre-sync-$(date +%Y%m%d) origin/main
git push origin backup/pre-sync-$(date +%Y%m%d)   # 网络通时

# 2. Fetch 上游
git fetch upstream

# 3. Rebase 本地修改
git rebase --onto upstream/main origin/main~N
# N = 本地独有的 commit 数量

# 4. 处理冲突，跳过 version bump commits

# 5. 编译验证
cargo build --release

# 6. 安装测试
# (按第二章步骤安装)
# (按第三章步骤测试)

# 7. Push
git push --force origin main
```

详细同步记录见 `doc/upstream-sync-2026-07-20.md`。
