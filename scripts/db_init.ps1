# Database init script (migration step 1: create schema)
#
# What it does:
#   1. check Docker daemon
#   2. start postgres:16 container and wait until healthy
#   3. apply backend/migrations/*.sql in filename order
#   4. idempotency check: re-apply the SAME migration, must pass again
#   5. print table / view / index / constraint counts
#
# NOTE: this file is ASCII-only on purpose (PowerShell reads .ps1 using the
# system locale; non-ASCII would be mis-decoded on GBK Windows and break parsing).
#
# Usage (project root, the folder containing docker-compose.yml):
#   pwsh -File scripts/db_init.ps1
#   pwsh -File scripts/db_init.ps1 -Reset     # drop data volume first

[CmdletBinding()]
param(
    [switch]$Reset,
    [string]$Container = "tenderagent-pg",
    [string]$Database        = "tender_agent",
    [string]$User      = "tender_app"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "  [X]  $msg" -ForegroundColor Red; exit 1 }

# --- 1. docker daemon ---
Step "1/5 check docker daemon"
docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Die "Docker daemon unavailable. Start Docker Desktop, wait for it to be ready, then rerun."
}
Ok "Docker daemon up (Server $((docker info --format '{{.ServerVersion}}')))"

# --- 2. start container ---
Step "2/5 start postgres container"
if ($Reset) {
    Warn "-Reset: dropping data volume (all data will be lost)"
    docker compose down -v | Out-Null
}
docker compose up -d | Out-Null
if ($LASTEXITCODE -ne 0) { Die "docker compose up failed" }

$deadline = (Get-Date).AddSeconds(150)
$state = ""
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $state = (docker inspect --format '{{.State.Health.Status}}' $Container 2>$null)
    if ($state -eq "healthy") { break }
}
if ($state -ne "healthy") {
    Die "container not healthy (state='$state'). Inspect with: docker compose logs pg"
}
Ok "container $Container healthy"

# --- 3. apply migrations (with idempotency check) ---
Step "3/5 apply migrations"
$sqlFiles = Get-ChildItem -Path "backend/migrations" -Filter "*.sql" | Sort-Object Name
if (-not $sqlFiles) { Die "no .sql files under backend/migrations" }

foreach ($f in $sqlFiles) {
    $inside = "/tmp/$($f.Name)"
    docker cp $f.FullName "${Container}:${inside}" | Out-Null
    # -v ON_ERROR_STOP=1 -> any statement failure exits non-zero (no half-applied schema)
    docker exec $Container psql -U $User -d $Database -v ON_ERROR_STOP=1 -q -f $inside
    if ($LASTEXITCODE -ne 0) { Die "migration $($f.Name) failed" }
    Ok "applied $($f.Name)"

    # --- 4. idempotency: run the same migration again ---
    docker exec $Container psql -U $User -d $Database -v ON_ERROR_STOP=1 -q -f $inside
    if ($LASTEXITCODE -ne 0) { Die "migration $($f.Name) is NOT idempotent (2nd run failed)" }
    Ok "re-run passed (idempotent)"
}

# --- 5. object summary ---
Step "4/5 object summary"
$q = "SELECT 'tables' AS kind, count(*)::text AS n FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' UNION ALL SELECT 'views', count(*)::text FROM information_schema.views WHERE table_schema='public' UNION ALL SELECT 'indexes', count(*)::text FROM pg_indexes WHERE schemaname='public' UNION ALL SELECT 'constraints', count(*)::text FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public' UNION ALL SELECT 'encoding', pg_encoding_to_char(encoding) FROM pg_database WHERE datname=current_database();"
docker exec $Container psql -U $User -d $Database -c $q

Step "5/5 tables and views"
docker exec $Container psql -U $User -d $Database -c "\dt"
docker exec $Container psql -U $User -d $Database -c "\dv"

Write-Host "`nDone. Local dev DSN:" -ForegroundColor Cyan
Write-Host "  postgresql://${User}:tender_app_dev@127.0.0.1:5433/${Database}" -ForegroundColor Cyan
