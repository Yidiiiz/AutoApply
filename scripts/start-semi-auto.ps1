param()
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$TaskPython = Join-Path $TaskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $TaskPython)) {
    throw 'Create the project virtual environment using docs/semi-auto-vscode.md first.'
}
Set-Location -LiteralPath $TaskRoot
& $TaskPython -m autoapply semi-auto
exit $LASTEXITCODE
