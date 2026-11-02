# Reports which variables in the root .env have a value. Never prints the values themselves.
# Usage: powershell -File scripts/check-env.ps1
param(
    [string]$File = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env")
)

$required = @("DATABASE_URL", "SUPABASE_URL", "SECRETS_ENCRYPTION_KEY")
$optional = @("TEST_DATABASE_URL", "SUPABASE_JWKS_URL", "SUPABASE_JWT_SECRET", "CORS_ORIGINS", "REDIS_URL")

if (-not (Test-Path $File)) {
    Write-Host ".env not found at $File. Copy .env.example to .env first." -ForegroundColor Red
    exit 1
}

$values = @{}
foreach ($line in Get-Content $File) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $values[$Matches[1]] = $Matches[2].Trim()
    }
}

$missing = 0
foreach ($name in $required) {
    if ($values[$name]) { Write-Host ("  set      {0}" -f $name) -ForegroundColor Green }
    else { Write-Host ("  MISSING  {0}  (required)" -f $name) -ForegroundColor Red; $missing++ }
}
foreach ($name in $optional) {
    if ($values[$name]) { Write-Host ("  set      {0}" -f $name) }
    else { Write-Host ("  empty    {0}  (optional)" -f $name) -ForegroundColor DarkGray }
}

if ($missing -gt 0) { exit 1 }
