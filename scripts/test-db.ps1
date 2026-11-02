# Runs the database tests against a throwaway in-memory Postgres (PGlite). Needs only Node.
# No Docker, no cloud project, nothing to clean up: the database disappears when this exits.
# Usage: powershell -File scripts/test-db.ps1 [extra pytest arguments]
# No param() block on purpose: pytest flags such as -k and -x must reach $args untouched.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$toolDir = Join-Path $PSScriptRoot "test-db"
$python = Join-Path $root "backend\.venv\Scripts\python.exe"
$port = if ($env:TEST_DB_PORT) { $env:TEST_DB_PORT } else { "54330" }

if (-not (Test-Path (Join-Path $toolDir "node_modules"))) {
    Write-Host "Installing PGlite..." -ForegroundColor Cyan
    npm --prefix $toolDir install --no-fund --no-audit | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
}

$server = Start-Process -FilePath (Join-Path $toolDir "node_modules\.bin\pglite-server.cmd") `
    -ArgumentList "--db=memory://", "--port=$port", "--host=127.0.0.1", "--max-connections=10" `
    -PassThru -WindowStyle Hidden

$code = 1
try {
    $url = "postgresql://postgres@127.0.0.1:$port/postgres?sslmode=disable"
    $probe = "import psycopg, sys, time`n" +
        "for _ in range(60):`n" +
        "    try:`n" +
        "        psycopg.connect(sys.argv[1], connect_timeout=2).close()`n" +
        "        sys.exit(0)`n" +
        "    except psycopg.OperationalError:`n" +
        "        time.sleep(0.5)`n" +
        "sys.exit(1)"
    & $python -c $probe $url
    if ($LASTEXITCODE -ne 0) { throw "PGlite did not start on port $port" }

    $env:TEST_DATABASE_URL = $url
    Push-Location (Join-Path $root "backend")
    try {
        & $python -m pytest tests/db -q -rs @args
        $code = $LASTEXITCODE
    } finally { Pop-Location }
} finally {
    Remove-Item Env:\TEST_DATABASE_URL -ErrorAction SilentlyContinue
    # npm's .cmd shim starts node as a child, so stop the whole process tree.
    & taskkill.exe /PID $server.Id /T /F 2>&1 | Out-Null
}
exit $code
