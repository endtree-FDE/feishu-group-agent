# 每天 09:00 处理 kb_inbox，并拉取配置里的飞书文档到 kb_staging（不自动替换正式 kb）
param([string]$Config = "agents\example.json", [string]$Name = "FeishuBot")
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
$a = New-ScheduledTaskAction -Execute $py -Argument "`"$base\kb_sync.py`" `"$base\$Config`" --inbox --pull-docs" -WorkingDirectory $base
Register-ScheduledTask -TaskName "$Name-KbSync" -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At 9am) -Force | Out-Null
"registered $Name-KbSync"
