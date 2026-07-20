# 上游同步记录：upstream v1.2.x → v1.6.16

> **日期**: 2026-07-20
> **操作者**: @china-sty
> **仓库**: xgit (fork of [git-ai-project/git-ai](https://github.com/git-ai-project/git-ai))

---

## 一、同步概览

| 项目 | 同步前 | 同步后 |
|------|--------|--------|
| **upstream/main** | `4c3be1c2b` (v1.2.x) | `6ab2adbb2` (v1.6.16) |
| **origin/main (fork)** | `ffef5987` (61 个本地 commit) | `303c1f55` (~15 个有效本地 commit + fixup) |
| **上游新增 commits** | — | **2192** 个 |
| **版本跨度** | v1.2.x | v1.5.3 → v1.5.13 → v1.6.0 → **v1.6.16** |
| **新增文件** | — | 466 个文件变更, +187,627 / -71,180 行 |

---

## 二、同步方法

### 2.1 备份策略

在操作前创建了**多层备份**，确保可以随时回滚：

| 备份 | 位置 | 说明 |
|------|------|------|
| `backup/20260720_204330` | 本地 | rebase 开始时自动创建 |
| `backup/pre-rebase-20260720` | 本地 + 远程 | 旧 `origin/main` 的精确快照 (`ffef5987`) |

**恢复方法**：
```bash
# 如果 push 后发现问题，可恢复到旧状态
git reset --hard backup/pre-rebase-20260720
git push --force origin main
```

### 2.2 Rebase 策略

采用 `git rebase --onto upstream/main` 将本地的 30 个 commit 逐个 rebase 到 upstream 最新 commit 之上。

**处理原则**：
- **Version bump 类 commit**（如 `chore: bump version to 1.2.xx`）：全部跳过，因为上游已是 v1.6.x，旧版本号无意义
- **Revert 类 commit**：如果后续 commit 重新应用了相同修改，跳过 revert
- **功能 commit**：逐个解决冲突，保留用户修改 + 合并上游变更

**最终保留的本地功能 commit**（约 15 个）：
1. `test` — 初始测试标记
2. `纯内存模式` — 数据库 fallback 改为内存模式
3. `修复上传失败，去掉push时拉去远程notes合并的逻辑` — 移除 pre-push notes fetch
4. `处理守护进程模式push未上传问题` — daemon push hook + upload-head-metrics
5. `修复claude hook error` — install.ps1 Windows 安装脚本修复 + config.json 生成
6. `qoder settings写入逻辑` — 新增 qoder agent hook
7. `stash支持` — rewrite_stash + inter_commit_move feature flags
8. `默认打开异步模式` — async_mode, git_hooks feature flags
9. `脚本覆盖config配置` — install.sh/ps1 默认 config 写入
10. `无外网权限服务器update指定内网路径` — 新增 update 命令
11. `trae test + token-usage引入` — 新增 trae agent + agent_presets monolith
12. `cb demo改造` — agent_service 目录重构
13. `增加install_inner脚本，引入cb demo` — Linux 安装脚本
14. `增加update日志` — update 命令日志
15. `fix: resolve compilation errors after rebase` — rebase 后编译兼容修复

### 2.3 编译兼容修复

Rebase 完成后 release build 出现 26 个编译错误，由上游 API 变更导致。主要修复：

| 问题 | 上游变更 | 修复方式 |
|------|----------|----------|
| `push_hooks` 模块缺失 | 上游删除了 pre-push hook 模块 | 移除调用，保留 upload-head-metrics |
| `update_script_url()` 方法缺失 | Config API 变更 | 直接使用默认 URL |
| `AmpPreset` / `AgentV1Preset` / `OpenCodePreset` 无 `.run()` 方法 | Presets 架构重构（`.run()` → `.parse()`） | 移除这三个 preset 的 match arm（上游已通过其他路径处理） |
| `agent_v1_preset` / `amp_preset` / `opencode_preset` 模块缺失 | 重构到 `presets/` 子目录 | 更新 import 路径 |
| `to_windows_git_bash_style_path` 缺失 | 重命名为 `normalize_windows_path_for_shell` | 全局替换 |
| `get_all_files_for_mock_ai` 缺失 | 函数所在文件被上游删除 | 新增辅助函数 |
| 缺少 `SystemTime` / `UNIX_EPOCH` / `AgentId` / `CheckpointKind` import | 旧的 re-export 路径变更 | 添加显式 import |

---

## 三、上游主要变更

### 3.1 架构级重构

#### Presets 模块化拆分
- **旧**: 所有 agent preset 散落在多个独立文件中（`agent_v1_preset.rs`, `amp_preset.rs` 等）
- **新**: 统一归入 `src/commands/checkpoint_agent/presets/` 目录，每个 preset 独立文件，通过 `AgentPreset` trait + `resolve_preset()` 函数统一调度
- **新增 preset**: cline, firebender, pi, mock_ai, known_human, mock_known_human, github_copilot (子模块)

#### 归因（Attribution）重写系统
全新 `src/authorship/rewrite*.rs` 模块族，支持复杂 Git 操作中的 AI 归因保留：
- `rewrite.rs` — 归因重写核心逻辑
- `rewrite_cherry_pick.rs` — cherry-pick 归因迁移
- `rewrite_reset.rs` — reset 操作归因恢复
- `rewrite_revert.rs` — revert 操作归因处理
- `rewrite_stash.rs` — stash push/apply/pop 归因追踪
- `conflict_resolution.rs` — rebase 冲突解决归因
- `hunk_shift.rs` — 代码块位移归因调整
- `diff_base.rs` — diff 基础算法
- `attribution_recovery.rs` — 归因恢复机制
- `background_agent.rs` — 后台归因代理

#### Daemon 守护进程重构
- **新事件系统**: `SemanticEvent` 驱动（CommitCreated, PushCompleted 等）
- **Stream Worker**: 异步 transcript 流处理
- **Transcript Sweep 触发**: 按 git 事件自动触发 transcript 扫描
- **Ingest 机制**: 守护进程摄入外部 agent 数据
- **内存优化**: 修复大数据 rebase 时的高内存占用问题

#### Notes 后端扩展
- **HTTP Notes Backend**: 支持通过 HTTP API 存储和同步 AI 归因数据
- **Notes 数据库隔离**: 测试环境数据库路径隔离
- **CAS Sync**: Content-Addressed Storage 同步队列

### 3.2 新增 Agent 支持

| Agent | 模块路径 | 说明 |
|-------|----------|------|
| Cline | `presets/cline.rs` | Cline AI 编程助手 |
| Codex | `presets/codex.rs` | OpenAI Codex CLI |
| Cursor | `presets/cursor.rs` | Cursor 编辑器 |
| Windsurf | `presets/windsurf.rs` | Windsurf IDE |
| Continue CLI | `presets/continue_cli.rs` | Continue.dev CLI |
| GitHub Copilot | `presets/github_copilot/` | VS Code Copilot |
| Amp | `presets/amp.rs` | Amp CLI |
| Agent V1 | `presets/agent_v1.rs` | 通用 agent 协议 v1 |
| Droid | `presets/droid.rs` | Droid agent |
| Firebender | `presets/firebender.rs` | Firebender agent |
| Gemini | `presets/gemini.rs` | Google Gemini |
| OpenCode | `presets/opencode.rs` | OpenCode agent |
| Pi | `presets/pi.rs` | Pi agent |
| AI Tab | `presets/ai_tab.rs` | AI Tab |
| Mock AI | `presets/mock_ai.rs` | 测试用 mock agent |

### 3.3 核心功能增强

#### Bash Checkpoints V2
- `src/commands/checkpoint_agent/bash_tool.rs` — Bash 工具分类
- `src/checkpoint_content_budget.rs` — Checkpoint 内容预算限制
- `feat/bash-checkpoints-v2-flag` — 新功能 flag
- `feat/bash-checkpoints-v2-record-only` — Record-only 模式

#### Transcript 流处理重构
- 新增 `src/streams/agents/` 目录，14 个 agent 专用 reader
- 支持多格式: ClaudeJsonl, GeminiJsonl, CodexJsonl, CursorJsonl, CopilotSessionJson, AmpThreadJson, OpenCodeSqlite 等
- Watermark 机制: ByteOffset, Hybrid, RecordIndex, Timestamp

#### Metrics / 遥测
- `src/metrics/db.rs` 重构 — 全局 metrics 数据库
- `src/commands/analyze/` — 新增 analyze 子命令（cube 分析, sessions 分析）
- `src/api/` — 新增内部 API（logs, notes）
- 遥测事件: VS Code Copilot token ingestion
- `ci_handlers.rs` — CI 环境集成

#### MDM / 设备管理
- 新增 agent installer: VisualStudio, JetBrains, Firebender, Droid, Pi, Windsurf, OpenCode
- Hook installer 系统重构
- Settings 路径候选机制

#### Windows 支持增强
- MSI 打包支持 (`feat/msi-pkg-*`)
- Windows hook 路径修复
- 升级流程 PowerShell 脚本优化

### 3.4 性能优化

- Gix (gitoxide) 库升级，改善 Git 操作性能
- Notes IO benchmark 新增
- Metrics DB 定期 prune（24h 间隔, 365 天保留）
- Checkpoint 内容预算限制（防止大文件 OOM）
- 内部 git 命令 trace2 抑制（减少子进程日志噪音）
- 内存泄漏修复（`devin/pd85-memory-leak-sanitized`）

### 3.5 安全与合规

- Superuser 检测与警告（root/sudo 安装警告）
- Sandbox 环境检测：沙箱中拒绝启动 daemon
- Prompt 安全扫描与脱敏
- `fix/release-apple-secrets` — macOS 签名安全

### 3.6 开发体验

- VSCode Extension 支持 blame lens
- OpenCode plugin 类型更新
- Claude Code skills 安装
- Dev 脚本 (`dev.sh`) 改进

---

## 四、本地修改保留清单

以下是在 rebase 过程中保留的所有本地功能修改：

| # | 功能 | 涉及文件 |
|---|------|----------|
| 1 | 纯内存数据库模式 | `src/metrics/db.rs`, `src/authorship/internal_db.rs` |
| 2 | 移除 pre-push notes fetch | `src/git/sync_authorship.rs` |
| 3 | Daemon push hook + upload-head-metrics | `src/daemon.rs` |
| 4 | Windows 安装脚本增强 + config.json 生成 | `install.ps1` |
| 5 | Qoder agent hook | `src/mdm/agents/qoder.rs`, `src/mdm/agents/mod.rs` |
| 6 | Stash + inter_commit_move feature flags | `src/feature_flags.rs`, `AGENTS.md` |
| 7 | Async mode + git hooks feature flags | `src/feature_flags.rs` |
| 8 | 安装脚本 config 覆盖 | `install.sh`, `install.ps1` |
| 9 | 内网 update 命令 | `src/commands/update.rs`, `src/commands/mod.rs` |
| 10 | Trae agent + agent_presets | `src/mdm/agents/trae.rs`, `src/commands/checkpoint_agent/agent_presets.rs` |
| 11 | agent_service (cb demo) | `agent_service/` 目录 |
| 12 | Linux install_inner 脚本 | 安装脚本 |
| 13 | Update 日志 | `src/commands/update.rs` |
| 14 | Qoder settings write | `src/commands/checkpoint_agent/agent_presets.rs` |

---

## 五、后续注意事项

1. **agent_presets.rs 是旧架构的 monolith 文件**（约 4000+ 行），上游已拆分为 `presets/` 目录下独立文件。后续如果上游继续更新 preset 逻辑，可能需要逐步将本地修改迁移到新的模块化架构。

2. **Qoder 和 Trae agent** 是用户自有的，上游不包含，需要持续维护。

3. **update.rs** 是用户自有的内网更新命令，上游已删除 update 模块（改用 upgrade.rs），需注意与上游 upgrade 命令的功能边界。

4. **Daemon 的 push 侧效应** 已大幅简化，`push_hooks` 模块被移除，后续如果要恢复 pre-push hook 功能，需要基于上游新架构重新实现。

5. **Feature flags** 合并了两边的 flag 集合，新增 flag 时需要同时更新 `define_feature_flags!` 宏调用和测试断言。

6. 建议定期从 upstream fetch 并 rebase，减少单次同步的冲突量。
