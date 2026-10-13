# Run from inside the qararix-portal folder:  powershell -ExecutionPolicy Bypass -File .\deploy.ps1
$ErrorActionPreference = "Continue"

if (-not (Test-Path ".\app.py") -or -not (Test-Path ".\portal.py")) {
    Write-Host "ERROR: app.py / portal.py not found. cd into the qararix-portal folder first." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Git is not installed. Install from https://git-scm.com/download/win and re-open PowerShell." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".\.git")) {
    Write-Host "ERROR: this folder is not a git repo. Run the 'first time only' commands from DEPLOY.md." -ForegroundColor Red
    exit 1
}
if (Test-Path ".\.env") {
    Write-Host "WARNING: a .env file exists. It is git-ignored, so it will NOT be pushed (good)." -ForegroundColor Yellow
}

git add -A
git status --short
$msg = "Update portal " + (Get-Date -Format "yyyy-MM-dd HH:mm")
git commit -m $msg
if ($LASTEXITCODE -ne 0) { Write-Host "Nothing new to commit (or commit failed). Continuing to push." -ForegroundColor Yellow }
git push origin main
if ($LASTEXITCODE -ne 0) { Write-Host "Push failed. Read the message above." -ForegroundColor Red; exit 1 }
Write-Host "Pushed. Now open Vercel -> Deployments and wait for the NEW deployment to become Ready." -ForegroundColor Green
