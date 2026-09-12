# Bind PostgreSQL 17 to localhost only (requires Administrator).
# Run: Right-click PowerShell -> Run as administrator -> execute this script.

$ErrorActionPreference = "Stop"
$ServiceName = "postgresql-x64-17"
$ConfPath = "C:\Program Files\PostgreSQL\17\data\postgresql.conf"

if (-not (Test-Path $ConfPath)) {
    Write-Error "postgresql.conf not found at $ConfPath"
}

$content = Get-Content $ConfPath -Raw
if ($content -match "listen_addresses\s*=\s*'localhost'") {
    Write-Host "listen_addresses already set to localhost"
} else {
    $updated = $content -replace "listen_addresses\s*=\s*'\*'", "listen_addresses = 'localhost'"
    Set-Content -Path $ConfPath -Value $updated -NoNewline
    Write-Host "Updated listen_addresses = 'localhost' in postgresql.conf"
}

Restart-Service $ServiceName
Start-Sleep -Seconds 2

$env:PGPASSWORD = "postgres"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -d postgres -c "SHOW listen_addresses;"
Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue

Write-Host "PostgreSQL restarted — localhost binding active after restart."
