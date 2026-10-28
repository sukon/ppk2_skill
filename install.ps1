# 安裝 PPK2 量測 skill 到 Claude Code 與 pi
#
#   pi         : 直接讀 $HOME\.agents\skills\（真檔安裝到這裡）
#   Claude Code: $HOME\.claude\skills\<name> 建立 junction 指到上面那份（與既有 skill 同一機制）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#
# 兩邊都是在啟動時掃描 skill 目錄，安裝後請重開 session 才會看到。

param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$name = 'ppk2-current-measurement'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $repo "skill\$name"
$dst = Join-Path $HOME ".agents\skills\$name"
$claudeLink = Join-Path $HOME ".claude\skills\$name"

function Get-LinkTarget([string]$path) {
    # 回傳：$null = 不存在；'' = 一般目錄（不是 junction）；其餘 = junction 的目標路徑
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $item = Get-Item -LiteralPath $path -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return '' }
    return [string]$item.Target
}

function Remove-SkillLink([string]$path) {
    # 只移除連結本身，不可碰目標內容：
    #   cmd rmdir 對 junction 有效（不加 /S）；PowerShell 5.1 的 Remove-Item -Recurse 會誤刪目標
    if (-not (Test-Path -LiteralPath $path)) { return }
    cmd /c rmdir "$path" 2>&1 | Out-Null
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $path) { throw "無法移除既有的連結：$path" }
}

if ($Uninstall) {
    Remove-SkillLink $claudeLink
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Write-Host "已移除：$claudeLink" -ForegroundColor Yellow
    Write-Host "已移除：$dst" -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $src)) { throw "找不到 skill 來源：$src" }

# 1) 安裝真檔（pi 與其他讀 ~/.agents/skills 的工具都用這份）
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse $src $dst
Write-Host "已安裝 skill：$dst"

# 2) Claude Code：目標固定，已經指向正確位置就不動它（避免反覆刪改 junction）
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $claudeLink) | Out-Null
$current = Get-LinkTarget $claudeLink
if ($current -eq $dst) {
    Write-Host "junction 已指向正確位置：$claudeLink"
}
else {
    if ($null -ne $current) { Remove-SkillLink $claudeLink }
    New-Item -ItemType Junction -Path $claudeLink -Target $dst | Out-Null
    Write-Host "已建立 junction：$claudeLink -> $dst"
}

Write-Host ''
Write-Host '完成。pi 與 Claude Code 都在啟動時掃描 skill，請重開 session。' -ForegroundColor Green
Write-Host 'skill 內的工作區路徑可用環境變數 PPK_USE_HOME 覆寫（預設 C:\Working\agents\ppk_use）。'
