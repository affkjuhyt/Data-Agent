# --- Kafka ACL Setup (Windows / PowerShell) ---------------------------
# PowerShell twin of kafka-setup.sh - no Git Bash required.
# Configures per-topic ACLs so each service can only access what it needs.
# Run AFTER docker compose up (Kafka must be healthy).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File infra/kafka/kafka-setup.ps1
#
# Idempotent: safe to re-run.

$ErrorActionPreference = "Stop"

# -- Load .env from repo root (two levels up) so KAFKA_* vars are available --
$EnvFile = Join-Path $PSScriptRoot "../../.env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name = $Matches[1]
            $val = $Matches[2].Trim()
            if ($val -match '^"(.*)"$') { $val = $Matches[1] }
            elseif ($val -match "^'(.*)'$") { $val = $Matches[1] }
            Set-Item -Path "Env:$name" -Value $val
        }
    }
}

$KafkaContainer = if ($env:KAFKA_CONTAINER) { $env:KAFKA_CONTAINER } else { "aeroflow-kafka" }
$Bootstrap = "localhost:9092"
$AdminUser = if ($env:KAFKA_ADMIN_USER) { $env:KAFKA_ADMIN_USER } else { "admin" }

if (-not $env:KAFKA_ADMIN_PASSWORD) {
    Write-Error "KAFKA_ADMIN_PASSWORD is required - copy .env.example to .env"
    exit 1
}
$AdminPass = $env:KAFKA_ADMIN_PASSWORD

Write-Host "Configuring Kafka ACLs..." -ForegroundColor Cyan
Write-Host "   Container : $KafkaContainer"
Write-Host "   Bootstrap : $Bootstrap"

# -- Write admin client properties into the container ------------------
# Build on the host and `docker cp` in - avoids cross-shell quoting issues.
# Removed at the end of this script to avoid credential leakage.
$propsTmp = New-TemporaryFile
$jaas = 'org.apache.kafka.common.security.plain.PlainLoginModule required ' +
        "username=`"$AdminUser`" password=`"$AdminPass`";"
@(
    "security.protocol=SASL_PLAINTEXT",
    "sasl.mechanism=PLAIN",
    "sasl.jaas.config=$jaas"
) | Set-Content -Path $propsTmp -Encoding ascii
& docker cp "$propsTmp" "${KafkaContainer}:/tmp/admin.props"
Remove-Item $propsTmp -ErrorAction SilentlyContinue
& docker exec $KafkaContainer chmod 600 /tmp/admin.props

$BaseArgs = "--bootstrap-server $Bootstrap --command-config /tmp/admin.props"

function Invoke-Kafka {
    param([string]$BashCmd)
    & docker exec $KafkaContainer bash -c $BashCmd
}

function New-Topic {
    param([string]$Topic, [string]$ExtraArgs, [string]$Desc)
    Invoke-Kafka "/opt/kafka/bin/kafka-topics.sh $BaseArgs --create --if-not-exists --topic $Topic $ExtraArgs"
    if ($LASTEXITCODE -eq 0) { Write-Host "  -> $Desc" }
}

function Add-Acl {
    param([string]$AclArgs, [string]$Desc)
    Invoke-Kafka "/opt/kafka/bin/kafka-acls.sh $BaseArgs --add $AclArgs"
    if ($LASTEXITCODE -eq 0) { Write-Host "  -> $Desc" }
}

# -- 1. Create topics explicitly ----------------------------------------
Write-Host ""
Write-Host "Creating topics..." -ForegroundColor Yellow

# replication-factor=1 is single-broker dev only; use 3 in production.
New-Topic "__consumer_offsets" "--partitions 50 --replication-factor 1 --config cleanup.policy=compact" `
    "topic: __consumer_offsets (50 partitions, compact)"
New-Topic "audit.auth.events" "--partitions 3 --replication-factor 1 --config retention.ms=2592000000 --config cleanup.policy=delete" `
    "topic: audit.auth.events (3 partitions, 30d retention)"
# Data-access audit: who ran which query. Higher volume than auth events (one
# per query vs one per login), so more partitions. Retention is set LONGER than
# auth events on purpose — this is the record an investigation reads back, and
# 30 days is shorter than most incidents take to surface. Align it with whatever
# retention policy applies to the data being queried.
New-Topic "audit.data.access" "--partitions 6 --replication-factor 1 --config retention.ms=7776000000 --config cleanup.policy=delete" `
    "topic: audit.data.access (6 partitions, 90d retention)"
New-Topic "release.pipeline.triggered" "--partitions 3 --replication-factor 1" "topic: release.pipeline.triggered"
New-Topic "release.pipeline.status"    "--partitions 3 --replication-factor 1" "topic: release.pipeline.status"
New-Topic "release.rollback.initiated" "--partitions 3 --replication-factor 1" "topic: release.rollback.initiated"

# -- 2. Enable ACLs (authorizer must be configured) ---------------------
Write-Host ""
Write-Host "Applying ACLs..." -ForegroundColor Yellow

Add-Acl "--allow-principal User:audit-bridge --operation Write --operation Describe --topic audit.auth.events" `
    "audit-bridge: Write + Describe on audit.auth.events"
Add-Acl "--allow-principal User:audit-consumer --operation Read --operation Describe --topic audit.auth.events" `
    "audit-consumer: Read + Describe on audit.auth.events"
Add-Acl "--allow-principal User:audit-consumer --operation Read --group audit-consumer-group" `
    "audit-consumer: Read on group audit-consumer-group"

# workflow-runtime PRODUCES data-access events; audit-consumer reads them.
# Without these the producer's send is rejected and every record is lost with
# only a client-side warning — the failure mode an audit can least afford.
Add-Acl "--allow-principal User:workflow-runtime --operation Write --operation Describe --topic audit.data.access" `
    "workflow-runtime: Write + Describe on audit.data.access"
Add-Acl "--allow-principal User:audit-consumer --operation Read --operation Describe --topic audit.data.access" `
    "audit-consumer: Read + Describe on audit.data.access"

foreach ($topic in @("release.pipeline.triggered", "release.pipeline.status", "release.rollback.initiated")) {
    Add-Acl "--allow-principal User:release-worker --operation Write --operation Read --operation Describe --operation Create --topic $topic" `
        "release-worker: Write + Read + Create on $topic"
}
Add-Acl "--allow-principal User:release-worker --operation Read --group release-worker-group" `
    "release-worker: Read on group release-worker-group"

# -- 3. Show current ACL summary ----------------------------------------
Write-Host ""
Write-Host "Current ACLs:" -ForegroundColor Yellow
Invoke-Kafka "/opt/kafka/bin/kafka-acls.sh $BaseArgs --list"

# -- 4. Remove temp credentials file -----------------------------------
& docker exec $KafkaContainer bash -c "rm -f /tmp/admin.props"
Write-Host ""
Write-Host "Removed /tmp/admin.props from container."

Write-Host ""
Write-Host "Kafka ACL setup complete." -ForegroundColor Green
Write-Host "----------------------------------------------------"
Write-Host "  audit-bridge    -> WRITE  audit.auth.events"
Write-Host "  audit-consumer  -> READ   audit.auth.events (group: audit-consumer-group)"
Write-Host "  release-worker  -> WRITE+READ+CREATE release.* topics"
Write-Host "  admin           -> ALL    (inter-broker + management)"
Write-Host "----------------------------------------------------"
