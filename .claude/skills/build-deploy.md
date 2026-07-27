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

## 四、提交推送

```bash
git add <files>
git commit -m "type: description

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin main
```

**提交类型**：`fix` / `feat` / `docs` / `chore` / `perf` / `refactor`

编译后 `Cargo.lock` 可能有变化，记得一起提交：
```bash
git add Cargo.lock
```

## 五、一站式脚本

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
