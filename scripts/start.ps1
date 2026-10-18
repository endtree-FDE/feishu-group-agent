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

# 本目录已经在跑就不要再启动，也不要结束它自己的 event 进程
$lock = Join-Path $base "state\bot.lock"
if (Test-Path $lock) {
  $old = (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)
  $old = "$old".Trim()
  if ($old -match '^\d+$' -and (Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue)) { "already running pid $old"; exit 0 }
}

# 配置里点名的旧版 pid 还活着：拒绝启动
foreach ($pf in @($cfg.conflict_pidfiles)) {
  if (-not $pf) { continue }
  $p = [string]$pf
  if (-not (Test-Path -LiteralPath $p)) { continue }
  $opid = (Get-Content -LiteralPath $p -ErrorAction SilentlyContinue | Select-Object -First 1)
  $opid = "$opid".Trim()
  if ($opid -match '^\d+$' -and (Get-Process -Id ([int]$opid) -ErrorAction SilentlyContinue)) {
    "REFUSED: conflicting instance still running (pid $opid from $pf)"; exit 4
  }
}

# 只结束本配置 profile 残留的 event consume / event _bus（用命令行匹配，不按进程名整批结束）
$profile = [string]$cfg.profile
if ($profile) {
  $esc = [regex]::Escape($profile)
  $profRe = "(?i)(?:^|\s)--profile(?:=|\s+)[`"']?$esc(?:[`"']|(?=\s|$))"
  $eventRe = '(?i)(?:^|\s)event\s+(consume|_bus)(?:\s|$)'
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
    $cl = [string]$_.CommandLine
    if (-not $cl) { return }
    $isLark = ([string]$_.Name -match '(?i)^lark-cli') -or ($cl -match '(?i)(^|[\\/])lark-cli(\.exe|\.cmd)?(\s|$)')
    if (-not $isLark) { return }
    if ($cl -notmatch $eventRe) { return }
    if ($cl -notmatch $profRe) { return }
    "stopping leftover lark-cli pid $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
New-Item -ItemType Directory -Force (Join-Path $base "logs") | Out-Null
$p = Start-Process -FilePath $py -ArgumentList "`"$base\agentbot.py`"", "`"$cfgPath`"" -WorkingDirectory $base -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$base\logs\stdout.log" -RedirectStandardError "$base\logs\stderr.log"
$p.Id | Out-File (Join-Path $base "bot.pid") -Encoding ascii
"started pid $($p.Id)"
