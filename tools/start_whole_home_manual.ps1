param(
    [switch]$AllowPaid
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$PythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Python virtual environment missing. Run the dependency installer first.'
}

$FrontendIndex = Join-Path $RepoRoot 'web\out\index.html'
if (-not (Test-Path -LiteralPath $FrontendIndex)) {
    throw 'Frontend build missing. Install dependencies explicitly, then run npm run build in web/.'
}
$FrontendBuildTime = (Get-Item -LiteralPath $FrontendIndex).LastWriteTimeUtc
$FrontendInputs = @(
    Get-ChildItem -LiteralPath (Join-Path $RepoRoot 'web\src') -Recurse -File
    Get-Item -LiteralPath (Join-Path $RepoRoot 'web\package.json')
    Get-Item -LiteralPath (Join-Path $RepoRoot 'web\package-lock.json')
    Get-Item -LiteralPath (Join-Path $RepoRoot 'web\next.config.ts')
    Get-Item -LiteralPath (Join-Path $RepoRoot 'web\tsconfig.json')
)
if ($FrontendInputs | Where-Object { $_.LastWriteTimeUtc -gt $FrontendBuildTime } | Select-Object -First 1) {
    throw 'Frontend build is stale. Run npm run build in web/ explicitly before starting.'
}

$env:FLOOR_DATA_DIR = Join-Path $RepoRoot 'data'
$env:FLOOR_WHOLE_HOME_MANUAL_SAFE = '1'
$env:FLOOR_WHOLE_HOME_ENABLE_AGENT_WORKFLOW = '0'
$env:FLOOR_WHOLE_HOME_ENABLE_EXTERNAL_REVIEW = '0'
$env:FLOOR_WHOLE_HOME_ENABLE_REFERENCE_WIP = '0'
$env:FLOOR_WHOLE_HOME_ENABLE_DEVELOPMENT_PAID = '0'
$env:FLOOR_ENGINE_DEVELOPMENT_AUTOPILOT = '0'
$env:FLOOR_WHOLE_HOME_MANUAL_ALLOW_PAID = if ($AllowPaid) { '1' } else { '0' }

Write-Host 'Floor Engine manual-safe mode: startup recovery/migration/reconcile disabled.'
if ($AllowPaid) {
    Write-Host 'Paid opt-in: ON. Only preview-confirmed /api/whole-home/manual/runs/commit is enabled.'
} else {
    Write-Host 'Paid opt-in: OFF. Preview is available; commit is blocked.'
}
& $PythonPath (Join-Path $RepoRoot 'serve.py')
exit $LASTEXITCODE
