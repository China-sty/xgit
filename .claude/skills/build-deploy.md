# 修改·编译·部署·提交流程

当需要修改代码、编译、安装部署、提交推送时使用。

## 环境前提

- 编译环境见 `doc/windows-rust-build-env.md`
- Rust / cargo: `D:\.cargo`
- MSVC: `D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207`
- Windows SDK: `C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0`

## 一、编译

用 cmd 执行（避免 PowerShell 干扰 cargo stderr 输出）。保存为 `D:\build.bat` 双击运行：

```cmd
set "CARGO_HOME=D:\.cargo"
set "RUSTUP_HOME=D:\.rustup"
set "PATH=D:\.cargo\bin;D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64;%PATH%"
set "LIB=D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\lib\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\um\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\ucrt\x64"
set "INCLUDE=D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\include;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\um;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\ucrt;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\shared"
cd /d D:\xgit
cargo build --release
pause
```

产物：`target\release\git-ai.exe`

## 二、部署

**默认使用 `install.ps1` 安装脚本：**

```powershell
$env:GIT_AI_LOCAL_BINARY = "D:\xgit\target\release\git-ai.exe"
.\install.ps1
```

设置 `GIT_AI_LOCAL_BINARY` 后脚本会跳过下载，直接使用本地编译好的二进制。脚本自动完成：
1. 停止 daemon，等待文件可用
2. 复制到 `~/.git-ai/bin/git-ai.exe` 和 `~/.git-ai/bin/git.exe`
3. 安装 hooks
4. 更新 PATH
5. 如需自动重启 daemon，设置 `$env:GIT_AI_RESTART_DAEMON_AFTER_INSTALL = '1'`

## 三、验证

```bash
git-ai --version
```

## 四、提交前安全检查

**Push 前必须检查是否有 LLM API Key 泄露！** 历史上有过把 key 硬编码提交的教训（`9a24b368`）。

### 扫描待提交的改动

```bash
# 检查 staged + unstaged 改动中的疑似 API Key
git diff --cached | Select-String -Pattern 'sk-[a-zA-Z0-9]{20,}|lsv2_pt_[a-zA-Z0-9]{20,}|x-api-key|api_key\s*=\s*"[^"]{20,}|LANGCHAIN_API_KEY|OPENAI_API_KEY|DEEPSEEK_API_KEY|FEISHU_WEBHOOK_URL\s*=\s*"https://open\.feishu'
```

### 已知泄露模式

| 模式 | 示例 | 来源 |
|------|------|------|
| `sk-*` | `sk-d59d5eb97421490aa65f8b484612f3d9` | OpenAI/DeepSeek/等 API Key |
| `lsv2_pt_*` | `lsv2_pt_c2765278f69f4c1d...` | LangSmith API Key |
| 硬编码 `api_key` | `"api_key": "sk-xxx"` | 配置文件 |
| Webhook URL | `https://open.feishu.cn/open-apis/bot/v2/hook/...` | 飞书机器人 |

### 正确做法

- **源码中**：用环境变量，配合 masking 输出（`serialize_masked_api_key`）
- **配置中**：`.env` 文件不应加入 git，确保在 `.gitignore` 中
- **Key 已泄露时**：立即在对应平台 revoke 该 key，生成新的

### 如果扫描命中

不要 push。先用 `git reset` 撤销 commit，移除 key，重新提交。

## 五、提交推送

```bash
# 1. 添加文件
git add <files>

# 2. 确认无 API Key 泄露
git diff --cached | findstr /i "sk- lsv2_pt_ api_key webhook"
# 如无输出 → 安全

# 3. 提交
git commit -m "type: description

Co-Authored-By: Claude <noreply@anthropic.com>"

# 4. 推送
git push origin main
```

**提交类型**：`fix` / `feat` / `docs` / `chore` / `perf` / `refactor`

编译后 `Cargo.lock` 可能有变化，记得一起提交：
```bash
git add Cargo.lock
```

## 六、push 前快速检查脚本

保存为 `D:\prepush.bat`，push 前运行：

```cmd
@echo off
cd /d D:\xgit
echo === Checking for API key leaks ===
git diff --cached | findstr /i "sk- lsv2_pt_ api_key x-api-key webhook"
if %errorlevel% equ 1 (
    echo [OK] No API key leaks detected
) else (
    echo [FAIL] API key leak detected! DO NOT PUSH.
    echo Run: git reset HEAD~1 to undo commit, remove keys, re-commit.
    pause
    exit /b 1
)
pause
```

## 七、一站式脚本（编译+部署）

保存为 `D:\ship.bat` 双击执行：

```cmd
@echo off
set "CARGO_HOME=D:\.cargo"
set "RUSTUP_HOME=D:\.rustup"
set "PATH=D:\.cargo\bin;D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64;%PATH%"
set "LIB=D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\lib\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\um\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\ucrt\x64"
set "INCLUDE=D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\include;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\um;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\ucrt;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\shared"
cd /d D:\xgit

echo === Building ===
cargo build --release
if %errorlevel% neq 0 (echo BUILD FAILED & pause & exit /b 1)

echo === Installing ===
powershell -Command "$env:GIT_AI_LOCAL_BINARY='D:\xgit\target\release\git-ai.exe'; $env:GIT_AI_RESTART_DAEMON_AFTER_INSTALL='1'; .\install.ps1"
if %errorlevel% neq 0 (echo INSTALL FAILED & pause & exit /b 1)

echo === Verifying ===
git-ai --version

echo === DONE ===
pause
```

## 调试

```bash
git-ai bg tail -n 50        # 查看守护进程日志
git-ai bg tail -f             # 实时跟踪
```

## 相关资源

| 文件 | 用途 |
|------|------|
| `doc/windows-rust-build-env.md` | Windows 编译环境搭建 |
| `.claude/skills/dev.md` | 开发调试技能 |
| `install.ps1` | 安装脚本（默认部署方式） |
