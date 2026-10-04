# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    为一个并行会话准备独立 worktree（含运行时数据、前端依赖、Pi 配置隔离）。

.DESCRIPTION
    多会话并行的唯一安全做法是「一个会话一个 worktree」：独立目录 + 独立分支 +
    独立端口 + 独立运行时数据。原因见 docs/多会话并行开工指令.md。

    本脚本只做准备工作，不启动任何服务、不改主仓库数据：
      1. git worktree add ..\ta-ws-<流> -b feat/<分支> main
      2. 复制被 gitignore 的必需文件：agent-config.json、.env、
         runtime/{workspaces,catalog,libraries}.json 与 runtime/projects（约 120 MB）
      3. frontend/node_modules 用 junction 共享（避免每个 worktree 装一遍）
      4. 复制 ~/.pi/agent → <worktree>\.pi-agent（Pi 会写 lock 文件，多实例共用会互锁）

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev\new-agent-worktree.ps1 -Stream a
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev\new-agent-worktree.ps1 -Stream c -SkipRuntime

.NOTES
    本文件以 UTF-8 with BOM 保存：Windows PowerShell 5.1 会按 ANSI 读取无 BOM 的脚本，
    中文会变乱码并直接语法报错。改动本文件后请保持 BOM。
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("a", "b", "c", "d", "e")]
    [string]$Stream,

    # 不复制 runtime 数据（只做单测的会话可以省这 120 MB）
    [switch]$SkipRuntime,

    # 不共享前端 node_modules（需要独立升级依赖时用）
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

# 会话 → 分支名（与 docs/多会话并行开工指令.md 的速查表一致）
$Branches = @{
    a = "feat/agent-validation"
    b = "feat/agent-tool-layer"
    c = "feat/fidelity-baselines"
    d = "feat/job-panel-ui"
    e = "feat/parse-chain-merge"
}
# 会话 → 端口（互不抢占；前端端口独立分配，避免与主实例的 5173/8799 撞车）
$Ports = @{ a = 8801; b = 8802; c = 8803; d = 8804; e = 8805 }
$WebPorts = @{ a = 5181; b = 5182; c = 5183; d = 5184; e = 5185 }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$baseBranch = "main"
$branch = $Branches[$Stream]
$worktree = (Join-Path (Split-Path $repoRoot -Parent) "ta-ws-$Stream")
$port = $Ports[$Stream]
$webPort = $WebPorts[$Stream]

# 真实用户目录：$env:USERPROFILE 可能被开发工具改写（本机实测被 DSH 会话目录顶掉），
# 因此优先用 Known Folder 解析，避免把错误的 Pi 配置目录复制进会话。
$userHome = [Environment]::GetFolderPath("UserProfile")
if (-not $userHome -or -not (Test-Path (Join-Path $userHome ".pi"))) {
    $userHome = $env:USERPROFILE
}

Write-Host "主仓库   : $repoRoot" -ForegroundColor Cyan
Write-Host "工作树   : $worktree" -ForegroundColor Cyan
Write-Host "分支     : $branch（基于 $baseBranch）" -ForegroundColor Cyan
Write-Host "端口     : 后端 $port / 前端 $webPort" -ForegroundColor Cyan
Write-Host ""

if (Test-Path $worktree) {
    throw "目录已存在：$worktree（先删除或换一个流名）"
}

Push-Location $repoRoot
try {
    git rev-parse --verify --quiet $baseBranch | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "基线分支不存在：$baseBranch" }

    Write-Host "[1/5] 创建 worktree 与分支…"
    git worktree add $worktree -b $branch $baseBranch
    if ($LASTEXITCODE -ne 0) { throw "git worktree add 失败" }

    Write-Host "[2/5] 复制配置与密钥（都被 gitignore，worktree 里没有）…"
    foreach ($file in @("agent-config.json", ".env")) {
        $source = Join-Path $repoRoot $file
        if (Test-Path $source) {
            Copy-Item $source (Join-Path $worktree $file) -Force
            Write-Host "      ✓ $file"
        } else {
            Write-Host "      - $file 不存在，跳过" -ForegroundColor Yellow
        }
    }

    Write-Host "[3/5] 灌入运行时数据（runtime/ 被 gitignore）…"
    $runtimeSource = Join-Path $repoRoot "runtime"
    $runtimeTarget = Join-Path $worktree "runtime"
    New-Item -ItemType Directory -Force $runtimeTarget | Out-Null
    if (-not $SkipRuntime) {
        foreach ($file in @("workspaces.json", "catalog.json", "libraries.json")) {
            $source = Join-Path $runtimeSource $file
            if (Test-Path $source) {
                Copy-Item $source (Join-Path $runtimeTarget $file) -Force
                Write-Host ("      ✓ {0} ({1:N1} MB)" -f $file, ((Get-Item $source).Length / 1MB))
            }
        }
        $projects = Join-Path $runtimeSource "projects"
        if (Test-Path $projects) {
            Copy-Item $projects (Join-Path $runtimeTarget "projects") -Recurse -Force
            Write-Host "      ✓ projects/（含解析包，约 33 MB）"
        }
        Write-Host "      ! 企业素材库 runtime\libraries（2.6 GB）未复制；需要时用 junction："
        Write-Host "        cmd /c mklink /J `"$runtimeTarget\libraries`" `"$runtimeSource\libraries`""
    } else {
        Write-Host "      - 已跳过（-SkipRuntime）" -ForegroundColor Yellow
    }

    Write-Host "[4/5] 共享前端依赖…"
    if (-not $SkipFrontend) {
        $modules = Join-Path $repoRoot "frontend\node_modules"
        $target = Join-Path $worktree "frontend\node_modules"
        if (Test-Path $modules) {
            cmd /c mklink /J "$target" "$modules" | Out-Null
            if (Test-Path $target) { Write-Host "      ✓ frontend\node_modules → junction" }
            else { Write-Host "      ! junction 创建失败，请在该 worktree 内 npm install" -ForegroundColor Yellow }
        } else {
            Write-Host "      ! 主仓库没有 frontend\node_modules，请在新 worktree 内 npm install" -ForegroundColor Yellow
        }
    } else {
        Write-Host "      - 已跳过（-SkipFrontend）" -ForegroundColor Yellow
    }

    Write-Host "[5/5] 隔离 Pi 配置目录（Pi 会写 settings.json.lock，多实例共用会互锁）…"
    $piSource = Join-Path $userHome ".pi\agent"
    $piTarget = Join-Path $worktree ".pi-agent"
    if (Test-Path $piSource) {
        Copy-Item $piSource $piTarget -Recurse -Force
        Write-Host "      ✓ .pi-agent/（从 $piSource 复制）"
    } else {
        Write-Host "      ! 找不到 $piSource，请先在主仓库跑一次 Pi 以生成配置" -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "完成。进入该会话目录后先执行：" -ForegroundColor Green
Write-Host "  cd $worktree"
Write-Host "  `$env:TENDER_AGENT_PORT = `"$port`""
Write-Host "  `$env:VITE_PORT = `"$webPort`""
Write-Host "  `$env:VITE_API_TARGET = `"http://127.0.0.1:$port`""
Write-Host "  `$env:PI_CODING_AGENT_DIR = `"$worktree\.pi-agent`""
Write-Host "  `$env:PYTHONIOENCODING = `"utf-8`"; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8"
Write-Host ""
Write-Host "单测用主仓库的 venv 即可（venv 被 gitignore，不必每个 worktree 装一份）："
Write-Host "  & $repoRoot\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_agent_runtime_contracts"
Write-Host ""
Write-Host "清理该会话：git worktree remove $worktree --force" -ForegroundColor DarkGray
