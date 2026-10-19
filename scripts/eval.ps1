# Replay answer-quality cases through the bot pipeline.
# Does not call Feishu and does not take the single-instance lock.
param(
    [string]$Config = "agents\example.json",
    [string]$Baseline = "evals\baseline\ci.json",
    [string]$OutDir = "evals\out",
    [string]$Provider = "fake",
    [string]$Tag = "ci"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$evalArgs = @(
    (Join-Path $root "evals\run.py"),
    "--config", $Config,
    "--baseline", $Baseline,
    "--out", $OutDir,
    "--provider", $Provider
)
if ($Tag) {
    $evalArgs += @("--tag", $Tag)
}
& python @evalArgs
exit $LASTEXITCODE
