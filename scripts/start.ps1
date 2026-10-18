# 启动机器人（后台）。用法: powershell -File scripts\start.ps1 [-Config agents\example.json]
# 启动前两步（watchdog.ps1、switch-from-old.ps1 都通过本脚本启动，所以同样走这里）：
#   1. conflict_pidfiles 里列出的旧进程还活着就拒绝启动，不结束那些进程。
#   2. 只结束本配置 profile 残留的 lark-cli event consume / event _bus。别的 profile 不动。
param([string]$Config = "agents\example.json")
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$env:NO_PROXY = "open.feishu.cn,.feishu.cn,.larksuite.com,localhost,127.0.0.1"; $env:no_proxy = $env:NO_PROXY

$cfgPath = $Config
if (-not [IO.Path]::IsPathRooted($cfgPath)) { $cfgPath = Join-Path $base $cfgPath }
if (-not (Test-Path -LiteralPath $cfgPath)) { "config not found: $cfgPath"; exit 2 }
$cfg = [IO.File]::ReadAllText($cfgPath) | ConvertFrom-Json
. (Join-Path $PSScriptRoot "botproc.ps1")
. (Join-Path $PSScriptRoot "rotatelog.ps1")

# 本目录已经在跑就不要再启动，也不要结束它自己的 event 进程。
# 锁里的编号若已被别的程序复用，不当成自己还活着。
$lock = Join-Path $base "state\bot.lock"
if (Test-Path $lock) {
  $old = (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)
  $old = "$old".Trim()
  if ($old -match '^\d+$') {
    $oldProc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$old)" -ErrorAction SilentlyContinue
    if ($oldProc -and (Test-OurAgent $oldProc $base)) { "already running pid $old"; exit 0 }
  }
}

# 配置里点名的旧版 pid 还活着：只有它看起来还是某个 agentbot 才拒绝启动。
# 编号被别的程序复用则不拒绝，也不结束那个进程。读不到命令行时宁可拒绝，避免同一个应用开两个接收进程。
foreach ($pf in @($cfg.conflict_pidfiles)) {
  if (-not $pf) { continue }
  $p = [string]$pf
  if (-not (Test-Path -LiteralPath $p)) { continue }
  $opid = (Get-Content -LiteralPath $p -ErrorAction SilentlyContinue | Select-Object -First 1)
  $opid = "$opid".Trim()
  if ($opid -notmatch '^\d+$') { continue }
  $oldProc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$opid)" -ErrorAction SilentlyContinue
  if (-not $oldProc) { continue }
  $cl = [string]$oldProc.CommandLine
  if ($cl -and $cl -notmatch '(?i)agentbot\.py') { continue }
  "REFUSED: conflicting instance still running (pid $opid from $pf)"; exit 4
}

# 只结束本配置 profile 残留的 event consume / event _bus。
# 结束前再读一次命令行，避免编号刚被别的进程复用。工作目录若明确是另一个目录，则不动。
Stop-LarkLeftovers $base ([string]$cfg.profile)

# 上次优雅停止留下的请求只在真正启动时清掉。已经在跑时不能清，否则正在退出的进程会被当成没人要求停止。
$shutdownReq = Join-Path $base "state\shutdown.request"
Remove-Item -LiteralPath $shutdownReq -Force -ErrorAction SilentlyContinue

$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
New-Item -ItemType Directory -Force (Join-Path $base "logs") | Out-Null
Rotate-LogFile (Join-Path $base "logs\stdout.log")
Rotate-LogFile (Join-Path $base "logs\stderr.log")
Rotate-LogFile (Join-Path $base "logs\watchdog.log")
$p = Start-Process -FilePath $py -ArgumentList "`"$base\agentbot.py`"", "`"$cfgPath`"" -WorkingDirectory $base -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$base\logs\stdout.log" -RedirectStandardError "$base\logs\stderr.log"
$p.Id | Out-File (Join-Path $base "bot.pid") -Encoding ascii
"started pid $($p.Id)"
