# 从旧目录切到本模板：停旧的（只停不删）→ 启动新的 → 90 秒内日志出现 ready 或 connected 算成功，否则自动切回旧的。
# 用法: powershell -File scripts\switch-from-old.ps1 -Old "D:\备份\旧机器人" -Config "agents\example.json"
# 用 Start-Process 调启动脚本，不要用管道（否则子进程继承管道，本脚本会一直卡住）。
param(
  [Parameter(Mandatory = $true)][string]$Old,
  [string]$Config = "agents\example.json"
)
$v2 = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path); $out = "$v2\logs\switch.log"
function L($m) { "$(Get-Date -Format s) $m" | Add-Content $out -Encoding utf8 }
function Run($f, $a = @(), [switch]$NoWait) { $x = @{ FilePath = "powershell.exe"; ArgumentList = (@('-NoProfile','-ExecutionPolicy','Bypass','-File', $f) + $a); WindowStyle = "Hidden" }; if (-not $NoWait) { $x.Wait = $true }; Start-Process @x }
if (-not (Test-Path $Old)) { "old directory not found: $Old"; exit 1 }
L "switch begin"; $startLen = (Get-Item "$v2\logs\bot.log" -ErrorAction SilentlyContinue).Length; if (-not $startLen) { $startLen = 0 }
Run "$Old\stop.ps1"; L "old stopped"; Start-Sleep 3
Run "$v2\scripts\start.ps1" @('-Config', $Config) -NoWait; Start-Sleep 5; L "new start issued"
$ok = $false; $t = ""
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep 3
  $fs = [IO.File]::Open("$v2\logs\bot.log", 'Open', 'Read', 'ReadWrite'); $fs.Seek($startLen, 'Begin') | Out-Null
  $t = (New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)).ReadToEnd(); $fs.Close()
  if ($t -match "feishu-websocket: connected|ready event_key=im.message.receive_v1") { $ok = $true; break }
  if ($t -match "REFUSED") { break }
}
if ($ok) { L "SUCCESS" } else { L "FAILED -> rollback`n$($t.Substring([Math]::Max(0,$t.Length-1500)))"; Run "$v2\scripts\stop.ps1"; Run "$Old\start.ps1" -NoWait; L "rolled back to old" }
