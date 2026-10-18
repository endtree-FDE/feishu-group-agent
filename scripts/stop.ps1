# 停止本目录的机器人，以及确实属于它的子进程（lark-cli event consume）。
# 不按父进程编号整棵结束：Windows 会复用 PID，别的机器人的进程可能挂在同一个编号下面。
# 只结束 bot.pid 里、命令行确认是本目录 agentbot.py 的进程；子进程还要命令行或工作目录对得上
# （lark-cli 的 profile，或本目录路径），并且创建时间晚于这个机器人。对不上的一律不动。
# 无论是否结束进程，都会清掉本目录的 bot.pid 和 state\bot.lock。
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $PSScriptRoot "botproc.ps1")
$pidf = Join-Path $base "bot.pid"
$lock = Join-Path $base "state\bot.lock"
function Clear-BotFiles {
  Remove-Item $pidf -Force -ErrorAction SilentlyContinue
  Remove-Item $lock -Force -ErrorAction SilentlyContinue
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
$profile = Get-ProfileOfAgent $proc $base
Stop-OwnedTree $proc $base $profile
Clear-BotFiles
"stopped $botId"
