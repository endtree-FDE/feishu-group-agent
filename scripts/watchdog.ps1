# 看门狗：心跳超过 3 分钟没更新，或进程不在，就重新启动。只写本地日志，不发任何消息。
param([string]$Config = "agents\example.json")
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$hb = Join-Path $base "state\heartbeat"; $wl = Join-Path $base "logs\watchdog.log"
$pidf = Join-Path $base "bot.pid"; $alive = $false
if (Test-Path $pidf) { $alive = [bool](Get-Process -Id ([int](Get-Content $pidf)) -ErrorAction SilentlyContinue) }
$stale = (-not (Test-Path $hb)) -or (([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) - [int](Get-Content $hb)) -gt 180
if ((-not $alive) -or $stale) {
  "$(Get-Date -Format s) restart (alive=$alive stale=$stale)" | Add-Content $wl -Encoding utf8
  if ($alive) { & (Join-Path $PSScriptRoot "stop.ps1") | Add-Content $wl -Encoding utf8 }
  & (Join-Path $PSScriptRoot "start.ps1") -Config $Config | Add-Content $wl -Encoding utf8
}
