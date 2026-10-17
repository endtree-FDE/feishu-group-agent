# 启动机器人（后台）。用法: powershell -File scripts\start.ps1 [-Config agents\example.json]
param([string]$Config = "agents\example.json")
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$env:NO_PROXY = "open.feishu.cn,.feishu.cn,.larksuite.com,localhost,127.0.0.1"; $env:no_proxy = $env:NO_PROXY
$lock = Join-Path $base "state\bot.lock"
if (Test-Path $lock) { $old = (Get-Content $lock).Trim(); if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) { "already running pid $old"; exit 0 } }
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
New-Item -ItemType Directory -Force (Join-Path $base "logs") | Out-Null
$p = Start-Process -FilePath $py -ArgumentList "`"$base\agentbot.py`"", "`"$base\$Config`"" -WorkingDirectory $base -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$base\logs\stdout.log" -RedirectStandardError "$base\logs\stderr.log"
$p.Id | Out-File (Join-Path $base "bot.pid") -Encoding ascii
"started pid $($p.Id)"
