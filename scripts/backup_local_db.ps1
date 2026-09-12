# Daily pg_dump backup for local quantara_prod (custom format, 14-day retention).

param(
    [string]$Database = "quantara_prod",
    [string]$PgHost = "localhost",
    [int]$Port = 5432,
    [string]$User = "quantara_prod",
    [string]$BackupRoot = "",
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $BackupRoot) {
    $BackupRoot = Join-Path $RepoRoot "backups\database"
}

$PgBin = "C:\Program Files\PostgreSQL\17\bin"
$pgDump = Join-Path $PgBin "pg_dump.exe"
if (-not (Test-Path $pgDump)) {
    Write-Error "pg_dump not found at $pgDump"
}

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $BackupRoot "${Database}_${stamp}.dump"

# Load DATABASE_URL password from .env when PGPASSWORD unset
if (-not $env:PGPASSWORD) {
    $envFile = Join-Path $RepoRoot ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match "^DATABASE_URL=(.+)$") {
                $url = $Matches[1]
                if ($url -match "://[^:]+:([^@]+)@") {
                    $env:PGPASSWORD = $Matches[1]
                }
                break
            }
        }
    }
}

Write-Host "Backing up $Database to $outFile"
& $pgDump -h $PgHost -p $Port -U $User -Fc -f $outFile $Database
if ($LASTEXITCODE -ne 0) {
    Write-Error "pg_dump failed with exit code $LASTEXITCODE"
}

$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem $BackupRoot -Filter "${Database}_*.dump" | Where-Object {
    $_.LastWriteTime -lt $cutoff
} | ForEach-Object {
    Write-Host "Removing old backup: $($_.Name)"
    Remove-Item $_.FullName -Force
}

Write-Host "Backup complete."
