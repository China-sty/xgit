$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-ErrorAndExit {
    param(
        [Parameter(Mandatory = $true)][string]$Message
    )
    Write-Host "Error: $Message" -ForegroundColor Red
    exit 1
}

function Write-Success {
    param(
        [Parameter(Mandatory = $true)][string]$Message
    )
    Write-Host $Message -ForegroundColor Green
}

function Write-Warning {
    param(
        [Parameter(Mandatory = $true)][string]$Message
    )
    Write-Host $Message -ForegroundColor Yellow
}

function Normalize-PathString {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    try {
        return ([IO.Path]::GetFullPath($Path.Trim())).TrimEnd('\').ToLowerInvariant()
    } catch {
        return ($Path.Trim()).TrimEnd('\').ToLowerInvariant()
    }
}

function Test-FileAvailable {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    try {
        $stream = [System.IO.File]::Open($Path, 'Open', 'Write', 'None')
        $stream.Close()
        return $true
    } catch {
        return $false
    }
}

function Stop-GitAiBackgroundService {
    param(
        [Parameter(Mandatory = $true)][string]$GitAiExe,
        [Parameter(Mandatory = $false)][switch]$Hard
    )

    if (-not (Test-Path -LiteralPath $GitAiExe)) {
        return $false
    }

    $args = @('bg', 'shutdown')
    if ($Hard) {
        $args += '--hard'
    }

    try {
        & $GitAiExe @args *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-GitAiManagedProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir
    )

    $targetPaths = @(
        (Normalize-PathString (Join-Path $InstallDir 'git-ai.exe')),
        (Normalize-PathString (Join-Path $InstallDir 'git.exe'))
    )

    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessId -ne $PID -and
            $_.ExecutablePath -and
            ($targetPaths -contains (Normalize-PathString $_.ExecutablePath))
        })

    return $processes
}

function Stop-GitAiManagedProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir
    )

    $processes = @(Get-GitAiManagedProcesses -InstallDir $InstallDir)
    if ($processes.Count -eq 0) {
        return $false
    }

    $pids = @($processes | Sort-Object ProcessId -Unique | Select-Object -ExpandProperty ProcessId)
    Write-Warning ("Stopping lingering git-ai processes: {0}" -f ($pids -join ', '))

    foreach ($pid in $pids) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction Stop
        } catch { }
    }

    return $true
}

function Wait-ForFileAvailable {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [Parameter(Mandatory = $false)][int]$MaxWaitSeconds = 300,
        [Parameter(Mandatory = $false)][int]$RetryIntervalSeconds = 5,
        [Parameter(Mandatory = $false)][int]$ForceKillAfterSeconds = 20
    )

    $elapsed = 0
    $gitAiExe = Join-Path $InstallDir 'git-ai.exe'

    [void](Stop-GitAiBackgroundService -GitAiExe $gitAiExe)

    while ($elapsed -lt $MaxWaitSeconds) {
        if (Test-FileAvailable -Path $Path) {
            return $true
        }

        if ($elapsed -ge $ForceKillAfterSeconds) {
            [void](Stop-GitAiBackgroundService -GitAiExe $gitAiExe -Hard)
            [void](Stop-GitAiManagedProcesses -InstallDir $InstallDir)
        }

        if (-not (Test-FileAvailable -Path $Path)) {
            if ($elapsed -eq 0) {
                Write-Host "Waiting for file to be available: $Path" -ForegroundColor Yellow
            }
            Start-Sleep -Seconds $RetryIntervalSeconds
            $elapsed += $RetryIntervalSeconds
        }
    }
    return $false
}

function Verify-Checksum {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string]$BinaryName
    )

    # Skip verification if no checksums are embedded
    if ($EmbeddedChecksums -eq '__CHECKSUMS_PLACEHOLDER__') {
        return
    }

    # Extract expected checksum for this binary
    $expected = $null
    $entries = $EmbeddedChecksums -split '\|'
    foreach ($entry in $entries) {
        if ($entry -match "^([0-9a-fA-F]+)\s+$([regex]::Escape($BinaryName))$") {
            $expected = $Matches[1]
            break
        }
    }

    if (-not $expected) {
        Write-ErrorAndExit "No checksum found for $BinaryName"
    }

    # Calculate actual checksum
    $hashCommand = Get-Command Get-FileHash -ErrorAction SilentlyContinue
    if ($null -ne $hashCommand) {
        $actual = (Get-FileHash -Path $File -Algorithm SHA256).Hash.ToLower()
    } else {
        $stream = [System.IO.File]::OpenRead($File)
        try {
            $sha256 = [System.Security.Cryptography.SHA256]::Create()
            $hashBytes = $sha256.ComputeHash($stream)
            $actual = ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').ToLower()
        } finally {
            $stream.Dispose()
            if ($sha256) {
                $sha256.Dispose()
            }
        }
    }

    if ($expected -ne $actual) {
        Remove-Item -Force -ErrorAction SilentlyContinue $File
        Write-ErrorAndExit "Checksum verification failed for $BinaryName`nExpected: $expected`nActual:   $actual"
    }

    Write-Success "Checksum verified for $BinaryName"
}

# GitHub repository details
# Replaced during release builds with the actual repository (e.g., "git-ai-project/git-ai")
# When set to __REPO_PLACEHOLDER__, defaults to "git-ai-project/git-ai"
$Repo = '__REPO_PLACEHOLDER__'
if ($Repo -eq '__REPO_PLACEHOLDER__') {
    $Repo = 'China-sty/xgit'
}

# Version placeholder - replaced during release builds with actual version (e.g., "v1.0.24")
# When set to __VERSION_PLACEHOLDER__, defaults to "latest"
$PinnedVersion = '__VERSION_PLACEHOLDER__'

# Embedded checksums - replaced during release builds with actual SHA256 checksums
# Format: "hash  filename|hash  filename|..." (pipe-separated)
# When set to __CHECKSUMS_PLACEHOLDER__, checksum verification is skipped
$EmbeddedChecksums = '__CHECKSUMS_PLACEHOLDER__'

# Ensure TLS 1.2 for GitHub downloads on older PowerShell versions
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch { }

function Get-Architecture {
    try {
        $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
        switch ($arch) {
            'X64' { return 'x64' }
            'Arm64' { return 'arm64' }
            default { return $null }
        }
    } catch {
        $pa = $env:PROCESSOR_ARCHITECTURE
        if ($pa -match 'ARM64') { return 'arm64' }
        elseif ($pa -match '64') { return 'x64' }
        else { return $null }
    }
}

function Get-StdGitPath {
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    $gitPath = $null
    if ($cmd -and $cmd.Path) {
        # Ensure we never return a path for git that contains git-ai (recursive)
        if ($cmd.Path -notmatch "git-ai") {
            $gitPath = $cmd.Path
        }
    }

    # If detection failed or was our own shim, try to recover from saved config
    if (-not $gitPath) {
        try {
            $cfgPath = Join-Path $HOME ".git-ai\config.json"
            if (Test-Path -LiteralPath $cfgPath) {
                $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
                if ($cfg -and $cfg.git_path -and ($cfg.git_path -notmatch 'git-ai') -and (Test-Path -LiteralPath $cfg.git_path)) {
                    $gitPath = $cfg.git_path
                }
            }
        } catch { }
    }

    # If still not found, fail with a clear message
    if (-not $gitPath) {
        Write-ErrorAndExit "Could not detect a standard git binary on PATH. Please ensure you have Git installed and available on your PATH. If you believe this is a bug with the installer, please file an issue at https://github.com/China-sty/xgit/issues."
    }

    try {
        & $gitPath --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'bad' }
    } catch {
        Write-ErrorAndExit "Detected git at $gitPath is not usable (--version failed). Please ensure you have Git installed and available on your PATH. If you believe this is a bug with the installer, please file an issue at https://github.com/China-sty/xgit/issues."
    }

    return $gitPath
}

function Find-GitBashExe {
    $candidates = New-Object System.Collections.Generic.List[string]

    $override = $env:GIT_AI_GIT_BASH_EXE
    if ($override -and (Test-Path -LiteralPath $override)) {
        $candidates.Add($override) | Out-Null
    }

    try {
        $gitCmd = Get-Command git.exe -All -ErrorAction SilentlyContinue
        # Iterate over all found git.exe, skipping our own shim
        foreach ($cmd in $gitCmd) {
            if ($cmd -and $cmd.Path -and ($cmd.Path -notmatch '(?i)\\.git-ai\\bin\\git\.exe$')) {
                $gitPath = $cmd.Path
                Write-Host "Found git.exe at: $gitPath" -ForegroundColor Gray
                
                # Try to find the 'Git' directory in the path
                if ($gitPath -match '(?i)(.*\\Git)(?:\\.*|$)') {
                    $gitRoot = $matches[1]
                    Write-Host "Inferred Git root from git.exe path: $gitRoot" -ForegroundColor Gray
                    Write-Host "Searching for bash.exe recursively in $gitRoot ..." -ForegroundColor Gray
                    try {
                        $foundBashes = Get-ChildItem -Path $gitRoot -Filter "bash.exe" -Recurse -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer }
                        foreach ($fb in $foundBashes) {
                            Write-Host "  -> Found candidate: $($fb.FullName)" -ForegroundColor DarkGray
                            $candidates.Add($fb.FullName) | Out-Null
                        }
                    } catch {
                        Write-Host "  -> Error searching in $gitRoot" -ForegroundColor DarkGray
                    }
                } else {
                    # Fallback to structural inference if 'Git' isn't in the path string
                    if ($gitPath -match '(?i)\\(cmd|bin)\\git\.exe$') {
                        $gitRoot = Split-Path -Parent (Split-Path -Parent $gitPath)
                        $candidates.Add((Join-Path $gitRoot 'bin\bash.exe')) | Out-Null
                        $candidates.Add((Join-Path $gitRoot 'usr\bin\bash.exe')) | Out-Null
                    } elseif ($gitPath -match '(?i)\\mingw64\\bin\\git\.exe$') {
                        $gitRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $gitPath))
                        $candidates.Add((Join-Path $gitRoot 'bin\bash.exe')) | Out-Null
                        $candidates.Add((Join-Path $gitRoot 'usr\bin\bash.exe')) | Out-Null
                    }
                }
                break # We found the real git, no need to check other commands
            }
        }
    } catch { }

    if ($env:ProgramFiles) { $candidates.Add((Join-Path $env:ProgramFiles 'Git\bin\bash.exe')) | Out-Null }
    if (${env:ProgramFiles(x86)}) { $candidates.Add((Join-Path ${env:ProgramFiles(x86)} 'Git\bin\bash.exe')) | Out-Null }
    if ($env:LOCALAPPDATA) { $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')) | Out-Null }

    try {
        $bashCmds = Get-Command bash.exe -All -ErrorAction SilentlyContinue
        foreach ($c in $bashCmds) {
            if ($c -and $c.Path) { $candidates.Add($c.Path) | Out-Null }
        }
    } catch { }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($p in $candidates) {
        if (-not $p) { continue }
        $full = $null
        try { $full = [IO.Path]::GetFullPath($p) } catch { $full = $p }
        if (-not $seen.Add($full.ToLowerInvariant())) { continue }
        if (-not (Test-Path -LiteralPath $full)) { continue }
        if ($full -notmatch '(?i)\\bin\\bash\.exe$') { continue }
        
        # Windows Subsystem for Linux (WSL) usually has its bash in System32. 
        # We explicitly exclude it here to avoid treating WSL bash as Git Bash.
        if ($full -match '(?i)\\System32\\bash\.exe$') { continue }
        
        # We assume if it's named \bin\bash.exe and not in System32, it's our target.
        return $full
    }

    return $null
}

# Ensure $PathToAdd is inserted before any PATH entry that contains "git" (case-insensitive)
# Updates Machine (system) PATH; if not elevated, emits a prominent error with instructions
function Set-PathPrependBeforeGit {
    param(
        [Parameter(Mandatory = $true)][string]$PathToAdd
    )

    $sep = ';'

    function NormalizePath([string]$p) {
        try { return ([IO.Path]::GetFullPath($p.Trim())).TrimEnd('\\').ToLowerInvariant() }
        catch { return ($p.Trim()).TrimEnd('\\').ToLowerInvariant() }
    }

    $normalizedAdd = NormalizePath $PathToAdd

    # Helper to build new PATH string with PathToAdd inserted before first 'git' entry
    function BuildPathWithInsert([string]$existingPath, [string]$toInsert) {
        $entries = @()
        if ($existingPath) { $entries = ($existingPath -split $sep) | Where-Object { $_ -and $_.Trim() -ne '' } }

        # De-duplicate and remove any existing instance of $toInsert
        $list = New-Object System.Collections.Generic.List[string]
        $seen = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($e in $entries) {
            $n = NormalizePath $e
            if (-not $seen.Contains($n) -and $n -ne $normalizedAdd) {
                $seen.Add($n) | Out-Null
                $list.Add($e) | Out-Null
            }
        }

        # Find first index that matches 'git' anywhere (case-insensitive)
        $insertIndex = 0
        for ($i = 0; $i -lt $list.Count; $i++) {
            if ($list[$i] -match '(?i)git') { $insertIndex = $i; break }
        }

        $list.Insert($insertIndex, $toInsert)
        return ($list -join $sep)
    }

    $userStatus = 'Skipped'
    try {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $newUserPath = BuildPathWithInsert -existingPath $userPath -toInsert $PathToAdd
        if ($newUserPath -ne $userPath) {
            [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
            $userStatus = 'Updated'
        } else {
            $userStatus = 'AlreadyPresent'
        }
    } catch {
        $userStatus = 'Error'
    }

    # Try to update Machine PATH
    $machineStatus = 'Skipped'
    try {
        $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $newMachinePath = BuildPathWithInsert -existingPath $machinePath -toInsert $PathToAdd
        if ($newMachinePath -ne $machinePath) {
            [Environment]::SetEnvironmentVariable('Path', $newMachinePath, 'Machine')
            $machineStatus = 'Updated'
        } else {
            # Nothing changed at Machine scope; still treat as Machine for reporting
            $machineStatus = 'AlreadyPresent'
        }
    } catch {
        # Access denied or not elevated; do NOT modify User PATH. Print big red error with instructions.
        $origGit = $null
        try { $origGit = Get-StdGitPath } catch { }
        $origGitDir = if ($origGit) { (Split-Path $origGit -Parent) } else { 'your Git installation directory' }
        Write-Host ''
        Write-Host 'ERROR: Unable to update the SYSTEM PATH (administrator rights required).' -ForegroundColor Red
        Write-Host 'Your PATH was NOT changed. To ensure git-ai takes precedence over Git:' -ForegroundColor Red
        Write-Host ("  1) Run PowerShell as Administrator and re-run this installer; OR") -ForegroundColor Red
        Write-Host ("  2) Manually edit the SYSTEM Path and move '{0}' before any entries containing 'Git' (e.g. '{1}')." -f $PathToAdd, $origGitDir) -ForegroundColor Red
        Write-Host "     Steps: Start -> type 'Environment Variables' -> 'Edit the system environment variables' -> Environment Variables ->" -ForegroundColor Red
        Write-Host ("            Under 'System variables', select 'Path' -> Edit -> Move '{0}' to the top (before Git) -> OK." -f $PathToAdd) -ForegroundColor Red
        Write-Host ''
        if ($userStatus -eq 'Updated' -or $userStatus -eq 'AlreadyPresent') {
            Write-Host 'User PATH was updated successfully, so git-ai will still take precedence for this account.' -ForegroundColor Yellow
        }
        $machineStatus = 'Error'
    }

    # Update current process PATH immediately for this session
    try {
        $procPath = $env:PATH
        $newProcPath = BuildPathWithInsert -existingPath $procPath -toInsert $PathToAdd
        if ($newProcPath -ne $procPath) { $env:PATH = $newProcPath }
    } catch { }

    return [PSCustomObject]@{
        UserStatus    = $userStatus
        MachineStatus = $machineStatus
    }
}

# Detect standard Git early and validate (fail-fast behavior)
$stdGitPath = Get-StdGitPath

# Detect architecture and OS
$arch = Get-Architecture
if (-not $arch) { Write-ErrorAndExit "Unsupported architecture: $([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)" }
$os = 'windows'

# Determine binary name and download URLs
$binaryName = "git-ai-$os-$arch"

# Determine release tag
# Priority: 1. Local binary override, 2. Pinned version (for release builds), 3. Environment variable, 4. "latest"
if (-not [string]::IsNullOrWhiteSpace($env:GIT_AI_LOCAL_BINARY)) {
    $releaseTag = 'local'
} elseif ($PinnedVersion -ne '__VERSION_PLACEHOLDER__') {
    # Version-pinned install script from a release
    $releaseTag = $PinnedVersion
    $downloadUrlExe = "https://github.com/$Repo/releases/download/$releaseTag/$binaryName.exe"
    $downloadUrlNoExt = "https://github.com/$Repo/releases/download/$releaseTag/$binaryName"
} elseif (-not [string]::IsNullOrWhiteSpace($env:GIT_AI_RELEASE_TAG) -and $env:GIT_AI_RELEASE_TAG -ne 'latest') {
    # Environment variable override
    $releaseTag = $env:GIT_AI_RELEASE_TAG
    $downloadUrlExe = "https://github.com/$Repo/releases/download/$releaseTag/$binaryName.exe"
    $downloadUrlNoExt = "https://github.com/$Repo/releases/download/$releaseTag/$binaryName"
} else {
    # Default to latest
    $releaseTag = 'latest'
    $downloadUrlExe = "https://github.com/$Repo/releases/latest/download/$binaryName.exe"
    $downloadUrlNoExt = "https://github.com/$Repo/releases/latest/download/$binaryName"
}

# Install directory: %USERPROFILE%\.git-ai\bin
$installDir = Join-Path $HOME ".git-ai\bin"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null

Write-Host ("Downloading git-ai (release: {0})..." -f $releaseTag)
$tmpFile = Join-Path $installDir "git-ai.tmp.$PID.exe"

function Try-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Url
    )
    try {
        # Disable progress bar to avoid extreme slowdown caused by PowerShell's
        # progress-stream rendering (can make downloads 10-50x slower).
        $oldProgressPreference = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try {
            Invoke-WebRequest -Uri $Url -OutFile $tmpFile -UseBasicParsing -ErrorAction Stop
        } finally {
            $ProgressPreference = $oldProgressPreference
        }
        return $true
    } catch {
        return $false
    }
}

# Track which download URL succeeded for checksum verification
$downloadedBinaryName = $null
if (-not [string]::IsNullOrWhiteSpace($env:GIT_AI_LOCAL_BINARY)) {
    if (-not (Test-Path -LiteralPath $env:GIT_AI_LOCAL_BINARY)) {
        Remove-Item -Force -ErrorAction SilentlyContinue $tmpFile
        Write-ErrorAndExit "Local binary not found at $($env:GIT_AI_LOCAL_BINARY)"
    }
    Copy-Item -Force -Path $env:GIT_AI_LOCAL_BINARY -Destination $tmpFile
    $downloadedBinaryName = "$binaryName.exe"
} elseif (Try-Download -Url $downloadUrlExe) {
    $downloadedBinaryName = "$binaryName.exe"
} elseif (Try-Download -Url $downloadUrlNoExt) {
    $downloadedBinaryName = $binaryName
}

if (-not $downloadedBinaryName) {
    Remove-Item -Force -ErrorAction SilentlyContinue $tmpFile
    Write-ErrorAndExit 'Failed to download binary (HTTP error)'
}

try {
    if ((Get-Item $tmpFile).Length -le 0) {
        Remove-Item -Force -ErrorAction SilentlyContinue $tmpFile
        Write-ErrorAndExit 'Downloaded file is empty'
    }
} catch {
    Remove-Item -Force -ErrorAction SilentlyContinue $tmpFile
    Write-ErrorAndExit 'Download failed'
}

# Verify checksum if embedded (release builds only)
Verify-Checksum -File $tmpFile -BinaryName $downloadedBinaryName

$finalExe = Join-Path $installDir 'git-ai.exe'

# Wait for git-ai.exe to be available if it exists and is in use
if (Test-Path -LiteralPath $finalExe) {
    if (-not (Wait-ForFileAvailable -Path $finalExe -InstallDir $installDir -MaxWaitSeconds 300 -RetryIntervalSeconds 5)) {
        Remove-Item -Force -ErrorAction SilentlyContinue $tmpFile
        Write-ErrorAndExit "Timeout waiting for $finalExe to be available. Please close any running git-ai processes and try again."
    }
}

Move-Item -Force -Path $tmpFile -Destination $finalExe
try { Unblock-File -Path $finalExe -ErrorAction SilentlyContinue } catch { }

# Create a shim so calling `git` goes through git-ai by PATH precedence
$gitShim = Join-Path $installDir 'git.exe'

# Wait for git.exe shim to be available if it exists and is in use
if (Test-Path -LiteralPath $gitShim) {
    if (-not (Wait-ForFileAvailable -Path $gitShim -InstallDir $installDir -MaxWaitSeconds 300 -RetryIntervalSeconds 5)) {
        Write-ErrorAndExit "Timeout waiting for $gitShim to be available. Please close any running git processes and try again."
    }
}

Copy-Item -Force -Path $finalExe -Destination $gitShim
try { Unblock-File -Path $gitShim -ErrorAction SilentlyContinue } catch { }

# Create a shim so calling `git-og` invokes the standard Git
$gitOgShim = Join-Path $installDir 'git-og.cmd'
$gitOgShimContent = "@echo off$([Environment]::NewLine)`"$stdGitPath`" %*$([Environment]::NewLine)"
Set-Content -Path $gitOgShim -Value $gitOgShimContent -Encoding ASCII -Force
try { Unblock-File -Path $gitOgShim -ErrorAction SilentlyContinue } catch { }

# Login user with install token if provided
$needLogin = $false
if ($env:INSTALL_NONCE -and $env:API_BASE) {
    try {
        & $finalExe exchange-nonce | Out-Host
        if ($LASTEXITCODE -ne 0) {
            $needLogin = $true
        }
    } catch {
        $needLogin = $true
    }
}

# Install hooks
Write-Host 'Setting up IDE/agent hooks...'
try {
    & $finalExe install-hooks | Out-Host
    Write-Success 'Successfully set up IDE/agent hooks'
} catch {
    Write-Warning "Warning: Failed to set up IDE/agent hooks. Please try running 'git-ai install-hooks' manually."
}

# Update PATH so our shim takes precedence over any Git entries
$skipPathUpdate = $env:GIT_AI_SKIP_PATH_UPDATE -eq '1'
if ($skipPathUpdate) {
    Write-Warning 'Skipping PATH updates because GIT_AI_SKIP_PATH_UPDATE=1'
    $pathUpdate = [PSCustomObject]@{
        UserStatus    = 'Skipped'
        MachineStatus = 'Skipped'
    }
} else {
    $pathUpdate = Set-PathPrependBeforeGit -PathToAdd $installDir
}
if ($pathUpdate.UserStatus -eq 'Updated') {
    Write-Success 'Successfully added git-ai to the user PATH.'
} elseif ($pathUpdate.UserStatus -eq 'AlreadyPresent') {
    Write-Success 'git-ai already present in the user PATH.'
} elseif ($pathUpdate.UserStatus -eq 'Error') {
    Write-Host 'Failed to update the user PATH.' -ForegroundColor Red
}

if ($pathUpdate.MachineStatus -eq 'Updated') {
    Write-Success 'Successfully added git-ai to the system PATH.'
} elseif ($pathUpdate.MachineStatus -eq 'AlreadyPresent') {
    Write-Success 'git-ai already present in the system PATH.'
} elseif ($pathUpdate.MachineStatus -eq 'Error') {
    Write-Host 'PATH update failed: system PATH unchanged.' -ForegroundColor Red
}

Write-Success "Successfully installed git-ai into $installDir"
Write-Success "You can now run 'git-ai' from your terminal"

# Configure Git Bash shell profiles so git-ai takes precedence over /mingw64/bin/git
# Git Bash (MSYS2/MinGW) prepends its own directories to PATH, which shadows
# the Windows PATH entry we set above. Writing to ~/.bashrc ensures git-ai's
# bin directory is prepended after Git Bash's own PATH setup.
$gitBashConfigured = $false
$gitBashAlreadyConfigured = $false
try {
    $bashrcPath = Join-Path $HOME '.bashrc'
    $bashProfilePath = Join-Path $HOME '.bash_profile'
    $pathCmd = 'export PATH="$HOME/.git-ai/bin:$PATH"'
    $markerString = '.git-ai/bin'

    # Detect if Git Bash is installed
    $gitBashInstalled = $false
    $gitBashExe = Find-GitBashExe
    if ($gitBashExe) { 
        $gitBashInstalled = $true 
        Write-Host "Found Git Bash at: $gitBashExe" -ForegroundColor Cyan
    } else {
        Write-Host "Warning: Could not find Git Bash (bash.exe) installation path. Git Bash profile will not be configured." -ForegroundColor Yellow
    }

    if ($gitBashInstalled) {
        # Determine which config file to update (prefer .bashrc, fall back to .bash_profile)
        $targetBashConfig = $null
        if (Test-Path -LiteralPath $bashrcPath) {
            $targetBashConfig = $bashrcPath
        } elseif (Test-Path -LiteralPath $bashProfilePath) {
            $targetBashConfig = $bashProfilePath
        } else {
            # No existing config; create .bashrc
            $targetBashConfig = $bashrcPath
        }

        # Check if already configured
        $alreadyPresent = $false
        if (Test-Path -LiteralPath $targetBashConfig) {
            $content = Get-Content -LiteralPath $targetBashConfig -Raw -ErrorAction SilentlyContinue
            if ($content -and $content.Contains($markerString)) {
                $alreadyPresent = $true
            }
        }

        if ($alreadyPresent) {
            $gitBashAlreadyConfigured = $true
        } else {
            $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
            $appendContent = "`n# Added by git-ai installer on $timestamp`n$pathCmd`n"
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::AppendAllText($targetBashConfig, $appendContent, $utf8NoBom)
            $gitBashConfigured = $true
        }
    }
} catch {
    Write-Host "Warning: Failed to configure Git Bash: $($_.Exception.Message)" -ForegroundColor Yellow
}

if ($gitBashConfigured) {
    Write-Success "Successfully configured Git Bash ($targetBashConfig)"
} elseif ($gitBashAlreadyConfigured) {
    Write-Success "Git Bash already configured ($targetBashConfig)"
}

# Write JSON config at %USERPROFILE%\.git-ai\config.json (only if it doesn't exist)
try {
    $configDir = Join-Path $HOME '.git-ai'
    $configJsonPath = Join-Path $configDir 'config.json'
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    if (-not (Test-Path -LiteralPath $configJsonPath)) {
        $cfg = @{
            git_path = $stdGitPath
            feature_flags = @{
                async_mode = $true
            }
        } | ConvertTo-Json -Depth 3 -Compress
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($configJsonPath, $cfg, $utf8NoBom)
    }
} catch {
    Write-Host "Warning: Failed to write config.json: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host 'Close and reopen your terminal and IDE sessions to use git-ai.' -ForegroundColor Yellow

# If nonce exchange failed, run interactive login
if ($needLogin) {
    Write-Host ''
    Write-Host 'Launching login...'
    & $finalExe login
}
