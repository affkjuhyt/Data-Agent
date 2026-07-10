# KB E2E — SELF-CONTAINED DEV helper (the original kb_e2e workflow).
#
# Brings up the WHOLE KB testing environment in one project from two files:
#   1. docker-compose.yml            — shared platform infra
#   2. docker-compose.kb_e2e.dev.yml — KB overlay (dev sizing, seed, kb services)
#
# Use this when you want a complete KB stack on your machine and nothing else is
# already running. (For attaching to infra someone else started, use kb_e2e.ps1.)
#
# Only the KB-relevant services are started — the full-platform app services
# (workflow-runtime, flow-builder, auth-api, frontend, etc.) are excluded.
#
# Usage:
#   pwsh infra/kb_e2e.dev.ps1                # bring up
#   pwsh infra/kb_e2e.dev.ps1 down          # tear down (keep volumes)
#   pwsh infra/kb_e2e.dev.ps1 down -v       # tear down + wipe volumes
#   pwsh infra/kb_e2e.dev.ps1 logs -f       # stream logs
#   pwsh infra/kb_e2e.dev.ps1 ps            # show service status

param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]] $ExtraArgs
)

$ErrorActionPreference = "Stop"

$composeFiles = @(
    "-f", "infra/docker-compose.yml",
    "-f", "infra/docker-compose.kb_e2e.dev.yml"
)

$envFile = "infra/.env.kb-testing"
if (Test-Path $envFile) {
    $composeFiles += @("--env-file", $envFile)
}

# Default action: bring up only KB-relevant services
$defaultServices = @(
    "postgres", "mongo", "qdrant", "neo4j", "minio", "kafka", "keycloak",
    "kong-migration", "kong", "kong-setup", "minio-init",
    "model-service", "kb-backend", "kb-ingestion-worker"
)

if ($ExtraArgs.Count -eq 0) {
    # No args → default bring-up
    $cmd = @("up", "-d", "--build") + $defaultServices
} elseif ($ExtraArgs[0] -in @("down", "logs", "ps", "stop", "restart", "config")) {
    # Lifecycle / inspection commands — no service list needed
    $cmd = $ExtraArgs
} else {
    # Pass through as-is (e.g. "up -d --no-deps model-service")
    $cmd = $ExtraArgs
}

Write-Host "docker compose $($composeFiles -join ' ') $($cmd -join ' ')" -ForegroundColor Cyan
docker compose @composeFiles @cmd
