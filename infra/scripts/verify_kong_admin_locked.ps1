# Verify Kong admin API is no longer reachable from places it shouldn't be.
# PowerShell twin of verify_kong_admin_locked.sh - no Git Bash required.
#
# Run AFTER `docker compose up -d` and the kong-setup container has run.
# Exit code: 0 if hardened correctly, non-zero (= number of failures) otherwise.
#
# Checks:
#   1. Host cannot reach admin on the removed port 8900           - CONNECT FAIL
#   2. A container on default network (workflow-runtime) cannot
#      reach the kong-admin-net IP                                - CONNECT FAIL
#   3. A container on kong-admin-net (auth-api) CAN reach admin   - 200
#
# Failure of (1) or (2) = security regression. Failure of (3) = stack broken,
# admin is unreachable even to the services that need it.

# NOTE: do NOT set $ErrorActionPreference = "Stop" - failing checks are expected
# and must not abort the run before the summary.

$KongAdminIp = if ($env:KONG_ADMIN_IP) { $env:KONG_ADMIN_IP } else { "172.30.0.10" }
$AdminUrl = "http://${KongAdminIp}:8001/status"

$script:Pass = 0
$script:Fail = 0

function Test-HostReachable {
    param([string]$Url)
    try {
        Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-ContainerReachable {
    param([string]$Container, [string]$Url)
    & docker exec $Container curl -sf --max-time 3 $Url *> $null
    return ($LASTEXITCODE -eq 0)
}

function Assert-ConnectFails {
    param([string]$Label, [bool]$Reachable)
    if ($Reachable) {
        Write-Host "X $Label - admin REACHABLE (regression)" -ForegroundColor Red
        $script:Fail++
    } else {
        Write-Host "OK $Label - connection refused (expected)" -ForegroundColor Green
        $script:Pass++
    }
}

function Assert-ConnectSucceeds {
    param([string]$Label, [bool]$Reachable)
    if ($Reachable) {
        Write-Host "OK $Label - admin reachable (expected)" -ForegroundColor Green
        $script:Pass++
    } else {
        Write-Host "X $Label - admin UNREACHABLE (stack broken)" -ForegroundColor Red
        $script:Fail++
    }
}

Write-Host "-- Kong admin API isolation verification --"
Write-Host ""

# 1. From host: the old port 8900 must NOT respond.
Assert-ConnectFails "Host cannot reach legacy admin port :8900" `
    (Test-HostReachable "http://localhost:8900/status")

$running = & docker ps --format '{{.Names}}'

# 2. From a default-net container that should NOT have admin access.
if ($running -contains "dataagent-workflow-runtime") {
    Assert-ConnectFails "workflow-runtime (default net only) cannot reach ${KongAdminIp}:8001" `
        (Test-ContainerReachable "dataagent-workflow-runtime" $AdminUrl)
} else {
    Write-Host "  ~ workflow-runtime not running; skipped default-net check"
}

# 3. From auth-api (admin-net) the admin MUST be reachable.
if ($running -contains "dataagent-auth-api") {
    Assert-ConnectSucceeds "auth-api (admin-net) can reach ${KongAdminIp}:8001/status" `
        (Test-ContainerReachable "dataagent-auth-api" $AdminUrl)
} else {
    Write-Host "  ~ auth-api not running; skipped admin-net check"
}

Write-Host ""
Write-Host "-- Result: $($script:Pass) passed, $($script:Fail) failed --"
exit $script:Fail
