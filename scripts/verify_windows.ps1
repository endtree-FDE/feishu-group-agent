# 部署后只读验证。用法:
#   powershell -File scripts\verify_windows.ps1 [-Config 路径1,路径2]
# 不发飞书消息，不启动 event consume，不写心跳，不删锁，不结束任何进程。
# 输出每一行都是 OK、WARN 或 FAIL。没有 FAIL 时退出码 0。
param([string[]]$Config = @(), [int]$EventTail = 50)
$ErrorActionPreference = "Continue"
if ($env:OS -ne "Windows_NT") {
  "FAIL platform 这个脚本只在 Windows 上读本机进程"
  exit 1
}
if ($EventTail -lt 1) { $EventTail = 50 }
if ($EventTail -gt 200) { $EventTail = 200 }

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
. (Join-Path $PSScriptRoot "botproc.ps1")

function Get-CreatedToken([int]$ProcessId) {
  # 与 proc_identity._win_created 相同：GetProcessTimes 的 64 位创建时间，十进制字符串。
  if ($ProcessId -le 0) { return "" }
  try {
    if (-not ('VerifyWin.Times' -as [type])) {
      Add-Type -Language CSharp -ErrorAction Stop -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace VerifyWin {
  public static class Times {
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a, bool i, int pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr h, out long c, out long e, out long k, out long u);
    public static string Created(int pid) {
      IntPtr h = OpenProcess(0x1000, false, pid);
      if (h == IntPtr.Zero) return "";
      try {
        long c, e, k, u;
        if (!GetProcessTimes(h, out c, out e, out k, out u)) return "";
        return ((ulong)c).ToString();
      } finally { CloseHandle(h); }
    }
  }
}
"@
    }
    return [string]([VerifyWin.Times]::Created($ProcessId))
  } catch {
    return ""
  }
}

function ConvertTo-ProcRecord($Proc) {
  if (-not $Proc) { return $null }
  $id = [int]$Proc.ProcessId
  return @{
    pid = $id
    cmdline = [string]$Proc.CommandLine
    created = (Get-CreatedToken $id)
    cwd = (Get-ProcessCwd $id)
  }
}

function Get-LogInfo([string]$Dir, [string]$Name) {
  $path = Join-Path (Join-Path $Dir "logs") $Name
  $exists = Test-Path -LiteralPath $path
  $size = 0
  if ($exists) {
    $item = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
    if ($item) { $size = [int64]$item.Length }
  }
  $backups = 0
  for ($i = 1; $i -le 5; $i++) {
    if (Test-Path -LiteralPath ($path + "." + $i)) { $backups++ }
  }
  return @{ name = $Name; exists = [bool]$exists; size = $size; backups = $backups }
}

function Read-EventsTail([string]$Dir) {
  $path = Join-Path (Join-Path $Dir "logs") "events.jsonl"
  if (-not (Test-Path -LiteralPath $path)) { return @() }
  $lines = @(Get-Content -LiteralPath $path -Tail $EventTail -Encoding UTF8 -ErrorAction SilentlyContinue)
  return @($lines | ForEach-Object { [string]$_ })
}

function Get-BotDirFromConfig([string]$ConfigPath) {
  $full = [IO.Path]::GetFullPath($ConfigPath)
  $parent = Split-Path -Parent $full
  if ((Split-Path -Leaf $parent) -eq "agents") { return (Split-Path -Parent $parent) }
  return $parent
}

$py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = "C:\Program Files\Python312\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { "FAIL python 找不到 python.exe"; exit 1 }
$report = Join-Path $root "verify_report.py"
if (-not (Test-Path -LiteralPath $report)) { "FAIL report 找不到 verify_report.py"; exit 1 }

$processError = $false
$rawProcs = @()
try {
  $rawProcs = @(Get-CimInstance Win32_Process -ErrorAction Stop)
} catch {
  $processError = $true
  $rawProcs = @()
}

$processes = New-Object System.Collections.Generic.List[object]
foreach ($proc in $rawProcs) {
  $cmd = [string]$proc.CommandLine
  if (-not $cmd) { continue }
  if ($cmd -notmatch '(?i)agentbot\.py' -and $cmd -notmatch '(?i)lark-cli') { continue }
  $record = ConvertTo-ProcRecord $proc
  if ($record) { $processes.Add($record) | Out-Null }
}

$bots = New-Object System.Collections.Generic.List[object]
$seenDir = @{}
function Add-Bot([string]$Dir, [string]$ConfigPath, [string]$Profile) {
  if (-not $Dir) { return }
  $key = $Dir.TrimEnd('\').ToLowerInvariant()
  if ($seenDir.ContainsKey($key)) { return }
  $seenDir[$key] = $true
  $lockPath = Join-Path (Join-Path $Dir "state") "bot.lock"
  $lockExists = Test-Path -LiteralPath $lockPath
  $lockText = ""
  $lockProc = $null
  if ($lockExists) {
    try { $lockText = [IO.File]::ReadAllText($lockPath) } catch { $lockText = "" }
    $first = (($lockText -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -First 1)
    $first = "$first".Trim()
    if ($first -match '^\d+$') {
      $id = [int]$first
      $again = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
      if ($again) {
        $one = ConvertTo-ProcRecord $again
        $lockProc = @{ alive = $true; pid = $id; cmdline = $one.cmdline; created = $one.created; cwd = $one.cwd }
      } else {
        $lockProc = @{ alive = $false; pid = $id; cmdline = ""; created = ""; cwd = "" }
      }
    }
  }
  $logs = @(
    (Get-LogInfo $Dir "stdout.log"),
    (Get-LogInfo $Dir "stderr.log"),
    (Get-LogInfo $Dir "watchdog.log"),
    (Get-LogInfo $Dir "bot.log"),
    (Get-LogInfo $Dir "events.jsonl")
  )
  $bots.Add(@{
    dir = $Dir
    config = $ConfigPath
    profile = $Profile
    lock_exists = [bool]$lockExists
    lock = $lockText
    lock_proc = $lockProc
    logs = $logs
    events_tail = @(Read-EventsTail $Dir)
  }) | Out-Null
}

foreach ($cfgPath in @($Config)) {
  if (-not $cfgPath) { continue }
  $full = $cfgPath
  if (-not [IO.Path]::IsPathRooted($full)) { $full = Join-Path $root $full }
  if (-not (Test-Path -LiteralPath $full)) { continue }
  $profile = ""
  try {
    $cfgObj = [IO.File]::ReadAllText($full) | ConvertFrom-Json
    $profile = [string]$cfgObj.profile
  } catch {
    $profile = ""
  }
  Add-Bot (Get-BotDirFromConfig $full) $full $profile
}

foreach ($proc in $processes) {
  $cmd = [string]$proc.cmdline
  if ($cmd -notmatch '(?i)agentbot\.py') { continue }
  if ($cmd -match '(?i)(--doctor|--check|--dry-run|--lock-test)') { continue }
  $cfgPath = ""
  $m = [regex]::Matches($cmd, '"([^"]+\.json)"')
  if ($m.Count -eq 0) { $m = [regex]::Matches($cmd, "'([^']+\.json)'") }
  if ($m.Count -gt 0) { $cfgPath = $m[$m.Count - 1].Groups[1].Value }
  $dir = ""
  $sm = [regex]::Match($cmd, '(?i)"([A-Za-z]:\\[^"]*agentbot\.py)"')
  if (-not $sm.Success) { $sm = [regex]::Match($cmd, "(?i)'([A-Za-z]:\\[^']*agentbot\.py)'") }
  if (-not $sm.Success) { $sm = [regex]::Match($cmd, '(?i)(?:^|\s)([A-Za-z]:\\[^\s"]+agentbot\.py)') }
  if ($sm.Success) { $dir = Split-Path -Parent $sm.Groups[1].Value }
  if (-not $dir -and $proc.cwd) { $dir = [string]$proc.cwd }
  $profile = ""
  if ($cfgPath -and (Test-Path -LiteralPath $cfgPath)) {
    try { $profile = [string](([IO.File]::ReadAllText($cfgPath) | ConvertFrom-Json).profile) } catch { $profile = "" }
    if (-not [IO.Path]::IsPathRooted($cfgPath) -and $dir) { $cfgPath = Join-Path $dir $cfgPath }
  }
  Add-Bot $dir $cfgPath $profile
}

$doctors = New-Object System.Collections.Generic.List[object]
foreach ($bot in $bots) {
  $cfgPath = [string]$bot.config
  $dir = [string]$bot.dir
  $label = [string]$bot.profile
  if (-not $label) { $label = Split-Path -Leaf $dir }
  if (-not $cfgPath -or -not (Test-Path -LiteralPath $cfgPath)) {
    $doctors.Add(@{ label = $label; config = $cfgPath; code = 0; output = "WARN config 没有配置文件，跳过诊断" }) | Out-Null
    continue
  }
  $agent = Join-Path $dir "agentbot.py"
  if (-not (Test-Path -LiteralPath $agent)) { $agent = Join-Path $root "agentbot.py" }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py
  $psi.Arguments = "`"$agent`" `"$cfgPath`" --doctor"
  $psi.WorkingDirectory = $dir
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
  $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
  $child = New-Object System.Diagnostics.Process
  $child.StartInfo = $psi
  [void]$child.Start()
  $outTask = $child.StandardOutput.ReadToEndAsync()
  $errTask = $child.StandardError.ReadToEndAsync()
  $child.WaitForExit()
  $text = (($outTask.Result) + "`n" + ($errTask.Result)).Trim()
  $doctors.Add(@{ label = $label; config = $cfgPath; code = [int]$child.ExitCode; output = $text }) | Out-Null
}

$payload = @{
  process_error = [bool]$processError
  processes = @($processes)
  bots = @($bots)
  doctors = @($doctors)
}
$json = $payload | ConvertTo-Json -Depth 8 -Compress
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $py
$psi.Arguments = "`"$report`""
$psi.WorkingDirectory = $root
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$psi.StandardOutputEncoding = [Text.Encoding]::UTF8
$psi.StandardErrorEncoding = [Text.Encoding]::UTF8
$child = New-Object System.Diagnostics.Process
$child.StartInfo = $psi
[void]$child.Start()
$outTask = $child.StandardOutput.ReadToEndAsync()
$errTask = $child.StandardError.ReadToEndAsync()
$writer = New-Object System.IO.StreamWriter($child.StandardInput.BaseStream, (New-Object System.Text.UTF8Encoding $false))
$writer.Write($json)
$writer.Close()
$child.WaitForExit()
$out = $outTask.Result
$err = $errTask.Result
if ($out) { Write-Output $out.TrimEnd() }
if ($err) { Write-Output $err.TrimEnd() }
exit [int]$child.ExitCode
