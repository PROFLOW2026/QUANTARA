# Restore quantara_prod from pg_dump custom-format backup.
# WARNING: drops and recreates the target database.

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$Database = "quantara_prod",
    [string]$Owner = "quantara_prod",
    [string]$PostgresUser = "postgres",
    [string]$PostgresPassword = "postgres",
    [string]$PgHost = "localhost",
    [int]$Port = 5432
)

$ErrorActionPreference = "Stop"
$PgBin = "C:\Program Files\PostgreSQL\17\bin"
$psql = Join-Path $PgBin "psql.exe"
$pgRestore = Join-Path $PgBin "pg_restore.exe"

if (-not (Test-Path $BackupFile)) {
    Write-Error "Backup file not found: $BackupFile"
}

$env:PGPASSWORD = $PostgresPassword

Write-Host "Terminating connections to $Database..."
& $psql -U $PostgresUser -h $PgHost -p $Port -d postgres -v ON_ERROR_STOP=1 -c @"
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '$Database' AND pid <> pg_backend_pid();
"@

Write-Host "Dropping and recreating $Database..."
& $psql -U $PostgresUser -h $PgHost -p $Port -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS `"$Database`" WITH (FORCE)"
& $psql -U $PostgresUser -h $PgHost -p $Port -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE `"$Database`" OWNER $Owner"

Write-Host "Restoring from $BackupFile ..."
& $pgRestore -h $PgHost -p $Port -U $PostgresUser -d $Database --no-owner --role=$Owner $BackupFile
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pg_restore exit code $LASTEXITCODE (often harmless for partial warnings)"
}

Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
Write-Host "Restore complete. Verify with: python scripts/verify_local_runtime.py"
