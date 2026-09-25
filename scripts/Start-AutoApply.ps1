$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install the project virtual environment before starting AutoApply.'
}
Set-Location -LiteralPath $projectRoot
& $pythonPath -m autoapply run
exit $LASTEXITCODE
