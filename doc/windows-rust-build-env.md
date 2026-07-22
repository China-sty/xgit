# Windows Rust 编译环境搭建指南

本指南记录在 Windows 11 上从零搭建 Rust 编译环境（安装到 D 盘）的完整过程。

## 环境概览

| 组件 | 安装路径 | 版本 |
|------|---------|------|
| Rust 工具链 | `D:\.cargo` / `D:\.rustup` | 1.97.1 (stable-x86_64-pc-windows-msvc) |
| VS Build Tools 2022 | `D:\vs2022\buildtools` | MSVC 14.44.35207 |
| Windows 11 SDK | `C:\Program Files (x86)\Windows Kits\10` | 10.0.26100.0 |
| crates.io 镜像 | `D:\.cargo\config.toml` | 清华 tuna (sparse HTTP) |

## 1. 安装 Rust 工具链

### 1.1 设置安装路径

为避免占用 C 盘空间，先设置环境变量将 Rust 安装到 D 盘：

```powershell
[Environment]::SetEnvironmentVariable("CARGO_HOME", "D:\.cargo", "User")
[Environment]::SetEnvironmentVariable("RUSTUP_HOME", "D:\.rustup", "User")
```

### 1.2 下载并安装

从 [https://rustup.rs](https://rustup.rs) 下载 `rustup-init.exe`，然后执行：

```powershell
$env:CARGO_HOME = "D:\.cargo"
$env:RUSTUP_HOME = "D:\.rustup"
.\rustup-init.exe -y --default-toolchain stable-x86_64-pc-windows-msvc
```

验证安装：

```powershell
D:\.cargo\bin\rustc.exe --version
D:\.cargo\bin\cargo.exe --version
```

## 2. 安装 VS Build Tools 2022（C 编译器）

Rust 的 `-msvc` 工具链需要 MSVC 链接器（`link.exe`）。部分 crate（如 `rusqlite` 的 `bundled` 特性）还需要 C 编译器（`cl.exe`）来编译 C 源码。

### 2.1 下载

从 [Visual Studio 下载页](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) 下载 `vs_buildtools.exe`。

### 2.2 安装到 D 盘

```powershell
vs_buildtools.exe `
  --installPath "D:\vs2022\buildtools" `
  --add Microsoft.VisualStudio.Workload.VCTools `
  --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
  --add Microsoft.VisualStudio.Component.Windows11SDK.26100 `
  --includeRecommended `
  --quiet --norestart --wait
```

> **注意**：VS 安装器的 `--wait` 参数有时不生效（引导程序启动后立即退出）。如果安装不完整，检查 `D:\vs2022\buildtools\VC\Tools\MSVC\` 下是否有 `cl.exe`。

## 3. 安装 Windows SDK

MSVC 链接器需要 Windows SDK 提供的系统库（如 `kernel32.lib`、`user32.lib`）。如果 VS Build Tools 安装后缺少这些文件，需要独立安装。

### 3.1 独立安装（备选方案）

从 [Windows SDK 下载页](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) 下载 `winsdksetup.exe`：

```powershell
winsdksetup.exe /q /norestart
```

SDK 默认安装到 `C:\Program Files (x86)\Windows Kits\10\`。

验证关键文件存在：

```powershell
Test-Path "${env:ProgramFiles(x86)}\Windows Kits\10\Lib\10.0.26100.0\um\x64\kernel32.Lib"
# 应返回 True
```

## 4. 配置 crates.io 镜像（国内必备）

国内网络直接访问 crates.io 极慢，需配置镜像源。

### 4.1 推荐配置：清华 tuna（sparse HTTP 协议）

编辑 `D:\.cargo\config.toml`：

```toml
[source.crates-io]
replace-with = 'tuna'

[source.tuna]
registry = "sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/"
```

### 4.2 踩过的坑

| 方案 | 问题 |
|------|------|
| 直连 crates.io | 下载极慢，`Updating crates.io index` 长时间无响应 |
| rsproxy.cn 镜像 | 仓库 `https://rsproxy.cn/crates.io-index/` 不可用（404） |
| 清华 git 协议 | `git fetch` 排队 300+ 位置，极慢且频繁 `exit code: 0xffffffff` |
| **sparse HTTP 协议** ✅ | 不走 git，下载快，推荐 |

## 5. 编译前设置环境变量

每次编译前需设置 MSVC + Windows SDK 的环境变量：

```powershell
# MSVC 路径
$msvcBin = "D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64"
$msvcInclude = "D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\include"
$msvcLib = "D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\lib\x64"

# Windows SDK 路径
$sdkRoot = "${env:ProgramFiles(x86)}\Windows Kits\10"
$sdkLib = "$sdkRoot\Lib\10.0.26100.0"
$sdkInc = "$sdkRoot\Include\10.0.26100.0"

# 设置环境变量
$env:PATH = "$msvcBin;D:\.cargo\bin;$env:PATH"
$env:CARGO_HOME = "D:\.cargo"
$env:RUSTUP_HOME = "D:\.rustup"
$env:LIB = "$msvcLib;$sdkLib\um\x64;$sdkLib\ucrt\x64"
$env:INCLUDE = "$msvcInclude;$sdkInc\um;$sdkInc\ucrt;$sdkInc\shared"

# 编译
cargo build --release
```

或用 **cmd** 执行（避免 PowerShell `2>&1` 对 cargo stderr 输出的干扰）：

```cmd
set "CARGO_HOME=D:\.cargo"
set "RUSTUP_HOME=D:\.rustup"
set "PATH=D:\.cargo\bin;D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64;%PATH%"
set "LIB=D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\lib\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\um\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.26100.0\ucrt\x64"
set "INCLUDE=D:\vs2022\buildtools\VC\Tools\MSVC\14.44.35207\include;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\um;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\ucrt;C:\Program Files (x86)\Windows Kits\10\Include\10.0.26100.0\shared"
cd /d D:\xgit
cargo build --release
```

> **推荐做法**：保存为 `.bat` 文件，双击即可编译，避免每次手动设置环境变量。

## 6. 常见错误排查

| 错误 | 原因 | 解决 |
|------|------|------|
| `cargo: command not found` | Rust 未安装或 PATH 未设置 | 安装 Rust 并添加 `D:\.cargo\bin` 到 PATH |
| `LINK : fatal error LNK1181: 无法打开输入文件"kernel32.lib"` | Windows SDK 未安装 | 安装 Windows 11 SDK（见第 3 节） |
| `Updating crates.io index` 长时间无响应 | 网络无法访问 crates.io | 配置国内镜像（见第 4 节） |
| `error: could not compile ... (build script)` | 缺少 C 编译器（`cl.exe`） | 安装 VS Build Tools C++ 工作负载 |
| PowerShell 中 `cargo build` 输出不刷新 | PowerShell `2>&1` 与原生 exe 的兼容问题 | 改用 cmd 执行编译（见第 5 节） |

## 7. 版本升级注意事项

- **MSVC 版本号**（如 `14.44.35207`）会随 VS Build Tools 更新变化，升级后需更新环境变量中的路径
- **Windows SDK 版本号**（如 `10.0.26100.0`）同理
- 可写一个脚本自动探测最新版本号来设置路径，避免硬编码
