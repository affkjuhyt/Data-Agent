# KB E2E stack helper
# Brings up the KB testing environment using two compose files:
#   1. docker-compose.yml        — shared platform infra
#   2. docker-compose.kb_e2e.yml — KB overlay (model-service, kb-backend, worker, etc.)
#
# Only the KB-relevant services are started — the full-platform app services
# (workflow-runtime, flow-builder, auth-api, frontend, etc.) are excluded.
#
# Usage:
#   pwsh infra/kb_e2e.ps1                # bring up
#   pwsh infra/kb_e2e.ps1 down          # tear down (keep volumes)
#   pwsh infra/kb_e2e.ps1 down -v       # tear down + wipe volumes
#   pwsh infra/kb_e2e.ps1 logs -f       # stream logs
#   pwsh infra/kb_e2e.ps1 ps            # show service status
#   pwsh infra/kb_e2e.ps1 --profile local-llm up -d --build <svc>  # with GPU LLM
#
# First-time auth setup (run once, after kong-setup completes):
#   (already included in default up — kong-setup runs automatically)

param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]] $ExtraArgs
)

$ErrorActionPreference = "Stop"

$composeFiles = @(
    "-f", "infra/docker-compose.yml",
    "-f", "infra/docker-compose.kb_e2e.yml"
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
