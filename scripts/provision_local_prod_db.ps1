# Provision dedicated local production PostgreSQL for QUANTARA (quantara_prod).
# Run once on Owner Windows machine. Requires postgres superuser (local).

param(
    [string]$ProdUser = "quantara_prod",
    [string]$ProdDb = "quantara_prod",
    [string]$PostgresUser = "postgres",
    [string]$PostgresPassword = "postgres",
    [string]$ProdPassword = ""
)

$ErrorActionPreference = "Stop"
$PgBin = "C:\Program Files\PostgreSQL\17\bin"
if (-not (Test-Path "$PgBin\psql.exe")) {
    Write-Error "PostgreSQL 17 bin not found at $PgBin"
}

if (-not $ProdPassword) {
    $ProdPassword = python -c "import secrets; print(secrets.token_urlsafe(32))"
}

$env:PGPASSWORD = $PostgresPassword
$psql = "$PgBin\psql.exe"

Write-Host "Creating role and database: $ProdDb / $ProdUser"

$roleSql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$ProdUser') THEN
    CREATE ROLE $ProdUser LOGIN PASSWORD '$ProdPassword';
  ELSE
    ALTER ROLE $ProdUser WITH PASSWORD '$ProdPassword';
  END IF;
END `$`$;
"@

& $psql -U $PostgresUser -h localhost -d postgres -v ON_ERROR_STOP=1 -c $roleSql

$dbExists = & $psql -U $PostgresUser -h localhost -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$ProdDb'"
if ($dbExists -match "1") {
    Write-Host "Database $ProdDb already exists - skipping CREATE DATABASE"
} else {
    & $psql -U $PostgresUser -h localhost -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE `"$ProdDb`" OWNER $ProdUser"
}

& $psql -U $PostgresUser -h localhost -d postgres -v ON_ERROR_STOP=1 -c "GRANT ALL PRIVILEGES ON DATABASE `"$ProdDb`" TO $ProdUser"

Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue

$conn = "postgresql://${ProdUser}:$ProdPassword@localhost:5432/$ProdDb"
Write-Host ""
Write-Host "quantara_prod provisioned."
Write-Host "DATABASE_URL=$conn"
Write-Host ""
Write-Host "Add DATABASE_URL to repo root .env (never commit)."
