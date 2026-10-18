# 停止本目录的机器人，以及确实属于它的子进程（lark-cli event consume）。
# 不按父进程编号整棵结束：Windows 会复用 PID，别的机器人的进程可能挂在同一个编号下面。
# 先请本进程自己退出（state\shutdown.request），等大约 20 秒。正在答的题留在 state.db，下次启动补答。
# 到点仍在跑，才用 Stop-OwnedTree。结束前再核对命令行和创建时间。对不上的一律不动。
# 无论是否结束进程，都会清掉本目录的 bot.pid 和 state\bot.lock。
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $PSScriptRoot "botproc.ps1")
$pidf = Join-Path $base "bot.pid"
$lock = Join-Path $base "state\bot.lock"
$shutdownReq = Join-Path $base "state\shutdown.request"
function Clear-BotFiles {
  Remove-Item $pidf -Force -ErrorAction SilentlyContinue
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
  Remove-Item $shutdownReq -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $pidf)) { "not running"; exit 0 }
$raw = "$((Get-Content $pidf -ErrorAction SilentlyContinue | Select-Object -First 1))".Trim()
if ($raw -notmatch '^\d+$') { Clear-BotFiles; "not running"; exit 0 }
$botId = [int]$raw
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$botId" -ErrorAction SilentlyContinue
if (-not $proc) { Clear-BotFiles; "not running"; exit 0 }
if (-not (Test-OurAgent $proc $base)) {
  Clear-BotFiles
  "pid $botId is not this bot; left it alone"
  exit 0
}
New-Item -ItemType Directory -Force (Join-Path $base "state") | Out-Null
Set-Content -LiteralPath $shutdownReq -Value ([string][int][double]::Parse((Get-Date -UFormat %s))) -Encoding ascii
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 1
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$botId" -ErrorAction SilentlyContinue
  if (-not $proc) { break }
  if (-not (Test-OurAgent $proc $base)) {
    Clear-BotFiles
    "pid $botId changed owner; left it alone"
    exit 0
  }
}
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$botId" -ErrorAction SilentlyContinue
if ($proc -and (Test-OurAgent $proc $base)) {
  $profile = Get-ProfileOfAgent $proc $base
  Stop-OwnedTree $proc $base $profile
}
Clear-BotFiles
"stopped $botId"
