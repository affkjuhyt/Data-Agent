# KB E2E stack helper
#
# Brings up ONLY the KB-owned services (model-service, kb-backend,
# kb-ingestion-worker + bootstrap jobs). The shared infrastructure
# (postgres, mongo, redis, qdrant, neo4j, minio, kafka, keycloak, kong) is
# expected to be ALREADY RUNNING from another stack (the base
# docker-compose.yml or the infra slice files). This stack just attaches to the
# shared `data-agent-network` and reuses those services — so it never clashes
# with them on container names, host ports, or volumes.
#
# Usage:
#   pwsh infra/kb_e2e.ps1                # bring up KB-owned services
#   pwsh infra/kb_e2e.ps1 down          # tear down (keep volumes)
#   pwsh infra/kb_e2e.ps1 down -v       # tear down + wipe KB volumes
#   pwsh infra/kb_e2e.ps1 logs -f       # stream logs
#   pwsh infra/kb_e2e.ps1 ps            # show service status
#
# Bring the SHARED infra up first, e.g.:
#   docker compose -f infra/docker-compose.yml up -d \
#     postgres mongo qdrant neo4j minio kafka keycloak kong-migration kong

param(
    [Parameter(ValueFromRemainingArguments)]
    [string[]] $ExtraArgs
)

$ErrorActionPreference = "Stop"

$composeFiles = @(
    "-f", "infra/docker-compose.kb_e2e.yml"
)

$envFile = "infra/.env.kb-testing"
if (Test-Path $envFile) {
    $composeFiles += @("--env-file", $envFile)
}

# Only the KB-owned services live in this stack now.
$defaultServices = @(
    "kb-seed", "minio-init", "kong-setup",
    "model-service", "kb-backend", "kb-ingestion-worker"
)

# The shared infra owns `data-agent-network`; we only join it. Warn early if it
# isn't there yet (otherwise compose fails with an opaque network error).
$bringUp = ($ExtraArgs.Count -eq 0) -or ($ExtraArgs[0] -eq "up")
if ($bringUp) {
    $net = docker network ls --filter "name=^data-agent-network$" --format "{{.Name}}" 2>$null
    if (-not $net) {
        Write-Host "WARNING: network 'data-agent-network' not found — is the shared infra up?" -ForegroundColor Yellow
        Write-Host "         Start it first, e.g.:" -ForegroundColor Yellow
        Write-Host "         docker compose -f infra/docker-compose.yml up -d postgres mongo qdrant neo4j minio kafka keycloak kong-migration kong" -ForegroundColor Yellow
    }
}

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
