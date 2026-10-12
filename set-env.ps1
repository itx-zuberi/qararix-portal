# Sets all Vercel Production environment variables for you.
# Run inside the qararix-portal folder:  powershell -ExecutionPolicy Bypass -File .\set-env.ps1
# NOTE: "Continue" on purpose - Vercel CLI prints its banner to stderr, which must not stop the script.
$ErrorActionPreference = "Continue"

function Plain([System.Security.SecureString]$s) {
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
function Fail($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "Node.js is required. Install the LTS version from https://nodejs.org then re-open PowerShell."
}

# Use vercel.cmd (not the .ps1 wrapper) so stderr output does not become a PowerShell error
$vc = $null
$cmd = Get-Command vercel.cmd -ErrorAction SilentlyContinue
if ($cmd) { $vc = $cmd.Source }
elseif (Test-Path "$env:APPDATA\npm\vercel.cmd") { $vc = "$env:APPDATA\npm\vercel.cmd" }
if (-not $vc) {
    Write-Host "Installing Vercel CLI..." -ForegroundColor Cyan
    npm install -g vercel
    if (Test-Path "$env:APPDATA\npm\vercel.cmd") { $vc = "$env:APPDATA\npm\vercel.cmd" }
}
if (-not $vc) { Fail "vercel.cmd not found. Close and re-open PowerShell, then run this script again." }

Write-Host "`n1) Vercel login (browser/prompt will open if needed)" -ForegroundColor Cyan
& $vc whoami 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $vc login
    & $vc whoami 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Vercel login did not complete." }
}
Write-Host "  logged in" -ForegroundColor Green

Write-Host "`n2) Linking this folder to the Vercel project" -ForegroundColor Cyan
& $vc link --yes --project qararix-portal --scope itx-zuberis-projects
if ($LASTEXITCODE -ne 0) { Fail "vercel link failed. Read the message above." }

Write-Host "`n3) Enter values (nothing is saved on disk)" -ForegroundColor Cyan
$ref = Read-Host "Supabase project ref [vyosnfunkwdrwzzcmgmk]"
if ([string]::IsNullOrWhiteSpace($ref)) { $ref = "vyosnfunkwdrwzzcmgmk" }
$dbpw = Plain (Read-Host "NEW Supabase database password" -AsSecureString)
$adm  = Plain (Read-Host "Admin password for portal login (min 10 chars)" -AsSecureString)
if ($dbpw.Length -lt 4) { Fail "Database password too short." }
if ($adm.Length -lt 10) { Fail "Admin password must be at least 10 characters." }

$enc = [uri]::EscapeDataString($dbpw)
$dbUrl = "postgresql://postgres.$ref`:$enc@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres?sslmode=require"

$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""

function Set-VercelEnv([string]$name, [string]$value) {
    & $vc env rm $name production --yes 2>&1 | Out-Null   # ok if it does not exist yet
    # Send the value on stdin WITHOUT a trailing newline
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $vc
    $psi.Arguments = "env add $name production"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.Write($value)
    $p.StandardInput.Close()
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) { Write-Host $out; Write-Host $err; Fail "Could not set $name" }
    Write-Host "  set $name" -ForegroundColor Green
}

Write-Host "`n4) Setting Production variables" -ForegroundColor Cyan
Set-VercelEnv "DATABASE_URL" $dbUrl
Set-VercelEnv "SECRET_KEY" $secret
Set-VercelEnv "QARARIX_ADMIN_PASSWORD" $adm
Set-VercelEnv "QARARIX_HTTPS" "1"

Write-Host "`nVariables now in Production:" -ForegroundColor Cyan
& $vc env ls production
Write-Host "`nNext: run .\deploy.ps1 to push the code (this creates the NEW deployment that uses these variables)." -ForegroundColor Yellow
