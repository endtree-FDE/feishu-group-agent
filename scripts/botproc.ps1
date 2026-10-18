# 判断进程是不是「这个目录里的机器人」。Windows 会复用 PID，不能只看父进程编号。
# 本文件只放函数。stop.ps1 / start.ps1 / watchdog.ps1 用点号引入，不要直接运行。

function ConvertTo-NormPath([string]$Path) {
  if (-not $Path) { return "" }
  return (($Path -replace '/', '\').Trim().TrimEnd('\')).ToLowerInvariant()
}

function Test-UnderBase([string]$Path, [string]$Base) {
  $n = ConvertTo-NormPath $Path
  $b = ConvertTo-NormPath $Base
  if (-not $n -or -not $b) { return $false }
  return ($n -eq $b) -or $n.StartsWith($b + '\')
}

function Test-ProfileInCommandLine([string]$CommandLine, [string]$Profile) {
  if (-not $Profile) { return $false }
  $esc = [regex]::Escape($Profile)
  $re = "(?i)(?:^|\s)--profile(?:=|\s+)[`"']?$esc(?:[`"']|(?=\s|$))"
  return [regex]::IsMatch([string]$CommandLine, $re)
}

function Get-ProcessCwd([int]$ProcessId) {
  # 读不到工作目录就返回空。调用方还要看命令行里的 profile 或本目录路径。
  try {
    if (-not ('BotProc.Cwd' -as [type])) {
      Add-Type -Language CSharp -ErrorAction Stop -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
namespace BotProc {
  public static class Cwd {
    const uint PROCESS_QUERY_INFORMATION = 0x0400;
    const uint PROCESS_VM_READ = 0x0010;
    [StructLayout(LayoutKind.Sequential)]
    struct PBI {
      public IntPtr Reserved1;
      public IntPtr PebBaseAddress;
      public IntPtr Reserved2_0;
      public IntPtr Reserved2_1;
      public IntPtr UniqueProcessId;
      public IntPtr InheritedFromUniqueProcessId;
    }
    [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(IntPtr h, int c, ref PBI p, int n, out int r);
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a, bool i, int pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int n, out IntPtr read);
    public static string CurrentDirectory(int pid) {
      IntPtr h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, false, pid);
      if (h == IntPtr.Zero) return "";
      try {
        var pbi = new PBI();
        int ret;
        if (NtQueryInformationProcess(h, 0, ref pbi, Marshal.SizeOf(typeof(PBI)), out ret) != 0) return "";
        if (IntPtr.Size != 8 || pbi.PebBaseAddress == IntPtr.Zero) return "";
        byte[] ptr = new byte[8];
        IntPtr got;
        if (!ReadProcessMemory(h, new IntPtr(pbi.PebBaseAddress.ToInt64() + 0x20), ptr, 8, out got)) return "";
        long procParams = BitConverter.ToInt64(ptr, 0);
        if (procParams == 0) return "";
        byte[] uni = new byte[16];
        if (!ReadProcessMemory(h, new IntPtr(procParams + 0x38), uni, 16, out got)) return "";
        int len = BitConverter.ToUInt16(uni, 0);
        long buf = BitConverter.ToInt64(uni, 8);
        if (len <= 0 || len > 2048 || buf == 0) return "";
        byte[] chars = new byte[len];
        if (!ReadProcessMemory(h, new IntPtr(buf), chars, len, out got)) return "";
        return Encoding.Unicode.GetString(chars).TrimEnd('\0');
      } catch { return ""; }
      finally { CloseHandle(h); }
    }
  }
}
"@
    }
    return [string]([BotProc.Cwd]::CurrentDirectory($ProcessId))
  } catch {
    return ""
  }
}

function Test-PathToken([string]$Text, [string]$Base) {
  # 整段路径要对上。c:\bots\a 不能命中 c:\bots\ab。
  $n = ConvertTo-NormPath $Text
  $b = ConvertTo-NormPath $Base
  if (-not $n -or -not $b) { return $false }
  $start = 0
  while ($start -lt $n.Length) {
    $i = $n.IndexOf($b, $start)
    if ($i -lt 0) { return $false }
    $beforeOk = $true
    if ($i -gt 0) {
      $prev = $n[$i - 1]
      $beforeOk = ($prev -eq ' ' -or $prev -eq '"' -or $prev -eq "'" -or $prev -eq '=' -or $prev -eq "`t")
    }
    $end = $i + $b.Length
    $afterOk = $true
    if ($end -lt $n.Length) {
      $nxt = $n[$end]
      $afterOk = ($nxt -eq '\' -or $nxt -eq ' ' -or $nxt -eq '"' -or $nxt -eq "'" -or $nxt -eq "`t")
    }
    if ($beforeOk -and $afterOk) { return $true }
    $start = $i + 1
  }
  return $false
}

function Test-OurAgent($Proc, [string]$Base) {
  # 命令行里要出现 agentbot.py，并且本目录是完整路径（bot 不能命中 bot2）。
  # 命令行只有文件名时，才看工作目录。编号被别的程序复用时这里为假。
  if (-not $Proc) { return $false }
  $cl = [string]$Proc.CommandLine
  if (-not $cl -or $cl -notmatch '(?i)agentbot\.py') { return $false }
  if (Test-PathToken $cl $Base) { return $true }
  $cwd = Get-ProcessCwd ([int]$Proc.ProcessId)
  return [bool]($cwd -and (Test-UnderBase $cwd $Base))
}

function Test-BelongsToBot($Proc, [string]$Base, [string]$Profile) {
  if (-not $Proc) { return $false }
  $cl = [string]$Proc.CommandLine
  if (Test-ProfileInCommandLine $cl $Profile) { return $true }
  if ($cl -and (Test-PathToken $cl $Base)) { return $true }
  $cwd = Get-ProcessCwd ([int]$Proc.ProcessId)
  return [bool]($cwd -and (Test-UnderBase $cwd $Base))
}

function Test-CreatedAfter($Proc, $ParentCreated) {
  if (-not $ParentCreated -or -not $Proc -or -not $Proc.CreationDate) { return $false }
  return $Proc.CreationDate -gt $ParentCreated
}

function Get-ConfigPathFromCommandLine([string]$CommandLine, [string]$Base) {
  $cands = @()
  foreach ($m in [regex]::Matches([string]$CommandLine, '"([^"]+\.json)"')) { $cands += $m.Groups[1].Value }
  foreach ($m in [regex]::Matches([string]$CommandLine, "'([^']+\.json)'")) { $cands += $m.Groups[1].Value }
  $nb = ConvertTo-NormPath $Base
  foreach ($c in $cands) {
    $full = $c
    if (-not [IO.Path]::IsPathRooted($full)) { try { $full = Join-Path $Base $c } catch { continue } }
    $nf = ConvertTo-NormPath $full
    if ($nf -eq $nb -or $nf.StartsWith($nb + '\')) { return $full }
  }
  return ""
}

function Get-ProfileOfAgent($Proc, [string]$Base) {
  $path = Get-ConfigPathFromCommandLine ([string]$Proc.CommandLine) $Base
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return "" }
  try {
    return [string](([IO.File]::ReadAllText($path) | ConvertFrom-Json).profile)
  } catch {
    return ""
  }
}

function Stop-IfStill([int]$ProcessId, [string]$Base, [string]$Profile, [bool]$IsRoot, $ParentCreated) {
  $again = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
  if (-not $again) { return }
  if ($IsRoot) {
    if (-not (Test-OurAgent $again $Base)) { "skip pid $ProcessId (not this bot)"; return }
  } else {
    if (-not (Test-CreatedAfter $again $ParentCreated)) { "skip pid $ProcessId (not created after this bot)"; return }
    if (-not (Test-BelongsToBot $again $Base $Profile)) { "skip pid $ProcessId (other bot)"; return }
  }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-OwnedTree($Root, [string]$Base, [string]$Profile) {
  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $byParent = @{}
  foreach ($p in $all) {
    $parentKey = [int]$p.ParentProcessId
    if (-not $byParent.ContainsKey($parentKey)) { $byParent[$parentKey] = @() }
    $byParent[$parentKey] += $p
  }
  $owned = New-Object System.Collections.Generic.List[object]
  $queue = New-Object System.Collections.Generic.Queue[int]
  $seen = @{}
  $queue.Enqueue([int]$Root.ProcessId)
  while ($queue.Count -gt 0) {
    $curId = $queue.Dequeue()
    if (-not $byParent.ContainsKey($curId)) { continue }
    foreach ($child in @($byParent[$curId])) {
      $childId = [int]$child.ProcessId
      if ($seen.ContainsKey($childId)) { continue }
      $seen[$childId] = $true
      if (-not (Test-CreatedAfter $child $Root.CreationDate)) { continue }
      if (-not (Test-BelongsToBot $child $Base $Profile)) { continue }
      $owned.Add($child) | Out-Null
      $queue.Enqueue($childId)
    }
  }
  for ($i = $owned.Count - 1; $i -ge 0; $i--) {
    Stop-IfStill ([int]$owned[$i].ProcessId) $Base $Profile $false $Root.CreationDate
  }
  Stop-IfStill ([int]$Root.ProcessId) $Base $Profile $true $null
}

function Test-LarkLeftover($Proc, [string]$Base, [string]$Profile) {
  if (-not $Proc -or -not $Profile) { return $false }
  $cl = [string]$Proc.CommandLine
  if (-not $cl) { return $false }
  $isLark = ([string]$Proc.Name -match '(?i)^lark-cli') -or ($cl -match '(?i)(^|[\\/])lark-cli(\.exe|\.cmd)?(\s|$)')
  if (-not $isLark) { return $false }
  if ($cl -notmatch '(?i)(?:^|\s)event\s+(consume|_bus)(?:\s|$)') { return $false }
  if (-not (Test-ProfileInCommandLine $cl $Profile)) { return $false }
  $cwd = Get-ProcessCwd ([int]$Proc.ProcessId)
  if ($cwd -and -not (Test-UnderBase $cwd $Base)) { return $false }
  return $true
}

function Stop-LarkLeftovers([string]$Base, [string]$Profile) {
  if (-not $Profile) { return }
  foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    if (-not (Test-LarkLeftover $p $Base $Profile)) { continue }
    $procId = [int]$p.ProcessId
    $again = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if (-not (Test-LarkLeftover $again $Base $Profile)) { "skip leftover pid $procId"; continue }
    "stopping leftover lark-cli pid $procId"
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  }
}
