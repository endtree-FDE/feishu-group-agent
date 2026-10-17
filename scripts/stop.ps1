# 停止机器人及其子进程（lark-cli 的 event consume）
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidf = Join-Path $base "bot.pid"
if (-not (Test-Path $pidf)) { "not running"; exit 0 }
$id = [int](Get-Content $pidf)
function Kill-Tree($p) { Get-CimInstance Win32_Process | ? { $_.ParentProcessId -eq $p } | % { Kill-Tree $_.ProcessId }; Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
Kill-Tree $id; Remove-Item $pidf; Remove-Item (Join-Path $base "state\bot.lock") -ErrorAction SilentlyContinue; "stopped $id"
