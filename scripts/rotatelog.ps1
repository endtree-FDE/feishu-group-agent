# 日志超过大约 2MB 时改名为 .1、.2 … 最多留 5 份。
# 文件正被别的进程占用、改名失败时什么都不做，也不结束任何进程。
function Rotate-LogFile([string]$Path, [int]$MaxBytes = 2000000, [int]$Backups = 5) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
  if (-not $item -or $item.Length -lt $MaxBytes) { return }
  for ($i = $Backups; $i -ge 1; $i--) {
    $src = if ($i -eq 1) { $Path } else { "$Path.$($i - 1)" }
    $dst = "$Path.$i"
    if (-not (Test-Path -LiteralPath $src)) { continue }
    try {
      if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force -ErrorAction Stop }
      Move-Item -LiteralPath $src -Destination $dst -ErrorAction Stop
    } catch {
      return
    }
  }
}
