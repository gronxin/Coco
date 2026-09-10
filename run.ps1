$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$frontend = Join-Path $PSScriptRoot "frontend\dist\index.html"
if (-not (Test-Path $python)) {
    throw "Please run: py -3 -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt"
}
if (-not (Test-Path $frontend)) {
    throw "Please run: cd frontend; npm ci; npm run build; cd .."
}
if ($args.Count -gt 0 -and $args[0] -eq "--check") { exit 0 }

& $python -m uvicorn analyzer.app:create_app --factory --host 127.0.0.1 --port 8765
exit $LASTEXITCODE
