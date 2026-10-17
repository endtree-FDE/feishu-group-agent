# 注册计划任务（由你本人运行。需要密码时会弹窗，脚本不保存密码）
#   -Mode logon  : 登录后启动 + 每 5 分钟看门狗（只在你登录时运行，不需要密码）
#   -Mode boot   : 开机就启动（不管有没有登录），需要你本人输入 Windows 密码
param([ValidateSet("logon","boot")][string]$Mode = "logon", [string]$Config = "agents\example.json", [string]$Name = "FeishuBot")
$base = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ps = "-NoProfile -ExecutionPolicy Bypass -File"
$aStart = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "$ps `"$base\scripts\start.ps1`" -Config $Config"
$aWd = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "$ps `"$base\scripts\watchdog.ps1`" -Config $Config"
$tWd = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 0)
if ($Mode -eq "logon") {
  Register-ScheduledTask -TaskName "$Name-Start" -Action $aStart -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $set -Force | Out-Null
  Register-ScheduledTask -TaskName "$Name-Watchdog" -Action $aWd -Trigger $tWd -Settings $set -Force | Out-Null
} else {
  $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "开机自启需要你的 Windows 密码（只交给任务计划程序，不保存到文件）"
  $pw = $cred.GetNetworkCredential().Password
  Register-ScheduledTask -TaskName "$Name-Start" -Action $aStart -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $set -User $cred.UserName -Password $pw -Force | Out-Null
  Register-ScheduledTask -TaskName "$Name-Watchdog" -Action $aWd -Trigger $tWd -Settings $set -User $cred.UserName -Password $pw -Force | Out-Null
}
"registered $Name-Start / $Name-Watchdog ($Mode)"
"如果还有旧的计划任务会在登录时启动旧目录，请先 Disable-ScheduledTask 停用它（不要删除），否则会和现在这个一起跑、重复回复。"
