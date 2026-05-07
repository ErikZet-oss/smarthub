# Smarthub API na porte 8001 (8000 casto obsadeny inym projektom)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Host 'Chyba .venv - v tomto priečinku spusti: python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt'
    exit 1
}
& .\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
