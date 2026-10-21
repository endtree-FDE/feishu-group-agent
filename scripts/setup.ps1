# 一键安装入口（Windows PowerShell 5.1）。
# 提问、检查和写配置都在 setup_wizard.py。本文件只负责找到 Python 并把它叫起来。
# 不保存密钥，不登录，不改已经有的登录。
param(
  [switch]$DryRun,
  [string]$Answers = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $root

function Find-Python {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { return [string]$cmd.Source }
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py -and $py.Source) { return [string]$py.Source }
  $fallback = "C:\Program Files\Python312\python.exe"
  if (Test-Path -LiteralPath $fallback) { return $fallback }
  return ""
}

$python = Find-Python
if (-not $python) {
  Write-Output "还没找到 Python。"
  Write-Output "为什么：安装向导和机器人都要用 Python 才能跑。"
  Write-Output "去哪找：这不在飞书里。请到 Python 官网安装 3.12，安装时勾选 Add python.exe to PATH。装完关掉这个窗口，重新打开，再运行 scripts\setup.ps1。"
  exit 2
}

$wizard = Join-Path $root "setup_wizard.py"
if (-not (Test-Path -LiteralPath $wizard)) {
  Write-Output "没有找到安装向导。请确认当前文件夹是这个仓库，里面有 setup_wizard.py。"
  exit 2
}

$cmdArgs = New-Object System.Collections.Generic.List[string]
[void]$cmdArgs.Add($wizard)
if ($DryRun) { [void]$cmdArgs.Add("--dry-run") }
if ($Answers) {
  [void]$cmdArgs.Add("--answers")
  [void]$cmdArgs.Add($Answers)
}
foreach ($extra in @($args)) {
  if ($extra) { [void]$cmdArgs.Add([string]$extra) }
}

& $python $cmdArgs.ToArray()
if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }
exit 0
