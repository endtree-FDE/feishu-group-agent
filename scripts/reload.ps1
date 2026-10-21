# 平滑重启：先走 stop.ps1（自己退出，到点才结束本目录的进程），确认没有接收进程，再 start.ps1 一次。
# 还有本目录的 agentbot 时不要再启动，避免两个接收进程。失败时把配置改回这次的备份。
# 不在这里扩大结束范围。结束进程仍只由原来的停止脚本做。
param([string]$Config = "agents\example.json")
$ErrorActionPreference = "Continue"
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $PSScriptRoot "botproc.ps1")

function Test-ReceiverLeft([string]$Base) {
  foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    if (Test-OurAgent $p $Base) { return $true }
  }
  return $false
}

function Restore-SwitchBackup([string]$Base) {
  $job = Join-Path $Base "state\switch_job.json"
  if (-not (Test-Path -LiteralPath $job)) { return }
  try {
    $parsed = [IO.File]::ReadAllText($job) | ConvertFrom-Json
  } catch {
    return
  }
  $backup = [string]$parsed.backup
  $cfg = [string]$parsed.cfg
  if (-not $backup -or -not $cfg) { return }
  if (-not (Test-UnderBase $backup $Base)) { return }
  if (-not (Test-UnderBase $cfg $Base)) { return }
  if (-not (Test-Path -LiteralPath $backup)) { return }
  if (-not (Test-Path -LiteralPath $cfg)) { return }
  Copy-Item -LiteralPath $backup -Destination $cfg -Force
}

& (Join-Path $PSScriptRoot "stop.ps1")
for ($i = 0; $i -lt 20; $i++) {
  if (-not (Test-ReceiverLeft $base)) { break }
  Start-Sleep -Seconds 1
}
if (Test-ReceiverLeft $base) {
  Restore-SwitchBackup $base
  $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
  if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
  if (Test-Path -LiteralPath $py) {
    & $py (Join-Path $base "feature_switch.py") --reload-failed --base $base --reason still_running
  }
  "refused: receiver still running"
  exit 3
}
& (Join-Path $PSScriptRoot "start.ps1") -Config $Config
if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit 0
