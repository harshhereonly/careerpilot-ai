# Quality gate: formatting, linting, type checks and tests for backend and frontend.
# Usage (from anywhere):
#   powershell -File scripts/check.ps1 [-SkipBackend] [-SkipDatabase] [-SkipFrontend]
param(
    [switch]$SkipBackend,
    [switch]$SkipDatabase,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $Name (exit code $LASTEXITCODE)" }
}

if (-not $SkipBackend) {
    Push-Location (Join-Path $root "backend")
    try {
        $py = ".\.venv\Scripts\python.exe"
        Invoke-Step "backend: ruff check" { & $py -m ruff check . }
        Invoke-Step "backend: ruff format --check" { & $py -m ruff format --check . }
        Invoke-Step "backend: mypy" { & $py -m mypy }
        Invoke-Step "backend: pytest (no database)" { & $py -m pytest -q --ignore=tests/db }
    } finally { Pop-Location }
    if (-not $SkipDatabase) {
        # A throwaway in-memory Postgres; never touches your Supabase project.
        Invoke-Step "backend: pytest (database, PGlite)" {
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test-db.ps1")
        }
    } else {
        Write-Host "==> backend: database tests SKIPPED by request" -ForegroundColor Yellow
    }
}

if (-not $SkipFrontend) {
    Push-Location (Join-Path $root "frontend")
    try {
        Invoke-Step "frontend: eslint" { npm run lint --silent }
        Invoke-Step "frontend: prettier --check" { npm run format:check --silent }
        Invoke-Step "frontend: tsc --noEmit" { npm run typecheck --silent }
        Invoke-Step "frontend: vitest" { npm test --silent }
    } finally { Pop-Location }
}

Write-Host "All checks passed." -ForegroundColor Green
