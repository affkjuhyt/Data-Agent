# -----------------------------------------------------------------------------
# model-backend.ps1 - PowerShell twin of model-backend.sh (no Git Bash needed).
#
# model-service is the single GATEWAY. Callers (ingestion, lightrag) never know the
# backend; they just hit model-service /embed, /rerank, /generate. This script flips
# those three between LOCAL (CPU models baked in the image) and a REMOTE Colab GPU
# server (cloudflared tunnel), then recreates model-service. No file edits.
#
# USAGE
#   powershell -ExecutionPolicy Bypass -File infra/model-backend.ps1 status
#   powershell -ExecutionPolicy Bypass -File infra/model-backend.ps1 local
#   powershell -ExecutionPolicy Bypass -File infra/model-backend.ps1 colab `
#       --embed-url URL --embed-key KEY --llm-url URL --llm-key KEY [--rerank-url URL]
#       (rerank shares the embed tunnel unless --rerank-url is given)
#       (omit --embed-* to leave embed local; omit --llm-* to leave LLM local)
#
# CAVEAT: the EMBED model determines the Qdrant vector dimension. Switching embed to
# a DIFFERENT model/dim requires re-embedding the corpus (reembed.py) or searches
# fail with a dim mismatch. Rerank and LLM switches are safe any time (no index).
# -----------------------------------------------------------------------------
param(
    [Parameter(Position = 0)]
    [string]$Command = "status",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

$ComposeFile = Join-Path $PSScriptRoot "docker-compose.kb-ui-testing.yml"
$ComposeArgs = @("compose", "-f", $ComposeFile)
$Svc = "model-service"
$Container = "kb-test-model-service"

function Invoke-Recreate {
    Write-Host "recreating $Svc ..."
    & docker @ComposeArgs up -d --no-deps $Svc
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed (exit $LASTEXITCODE)" }
    # docker inspect on a not-yet-running container writes to stderr; redirecting
    # native stderr under EAP=Stop would terminate, so soften it locally.
    $ErrorActionPreference = 'SilentlyContinue'
    Write-Host -NoNewline "waiting for healthy "
    for ($i = 0; $i -lt 45; $i++) {
        $status = & docker inspect -f '{{.State.Health.Status}}' $Container 2>$null
        if ($status -eq "healthy") { Write-Host " ok"; return }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 2
    }
    Write-Host ""
}

function Get-ContainerEnv {
    param([string]$Name)
    # Read a single env var as seen inside the running container.
    $ErrorActionPreference = 'SilentlyContinue'  # tolerate native stderr under redirect
    (& docker exec $Container sh -c "echo `$$Name" 2>$null)
}

function Invoke-Status {
    $ErrorActionPreference = 'SilentlyContinue'  # docker exec/env may write to stderr
    Write-Host "-- routing (env on $Container) --"
    $envLines = & docker exec $Container env 2>$null |
        Select-String -Pattern 'REMOTE_EMBED_URL|REMOTE_RERANK_URL|REMOTE_LLM_URL|EMBED_MODEL|RERANK_MODEL' |
        Sort-Object
    foreach ($line in $envLines) { Write-Host "  $line" }

    Write-Host "-- reachability --"
    $embedUrl = Get-ContainerEnv "REMOTE_EMBED_URL"
    $llmUrl   = Get-ContainerEnv "REMOTE_LLM_URL"
    $llmKey   = Get-ContainerEnv "REMOTE_LLM_KEY"

    if ($embedUrl) {
        $code = Get-HttpCode -Url "$embedUrl/health"
        $state = if ($code -eq "000") { "DOWN" } else { "up" }
        Write-Host "  embed tunnel  $embedUrl  ->  HTTP $code  ($state)"
    } else {
        Write-Host "  embed: LOCAL"
    }

    if ($llmUrl) {
        $code = Get-HttpCode -Url "$llmUrl/v1/models" -BearerToken $llmKey
        $state = if ($code -eq "000") { "DOWN" } else { "up" }
        Write-Host "  llm   tunnel  $llmUrl  ->  HTTP $code  ($state)"
    } else {
        Write-Host "  llm:   LOCAL"
    }
}

function Get-HttpCode {
    param([string]$Url, [string]$BearerToken)
    $headers = @{}
    if ($BearerToken) { $headers["Authorization"] = "Bearer $BearerToken" }
    try {
        $resp = Invoke-WebRequest -Uri $Url -Headers $headers -TimeoutSec 8 -UseBasicParsing -ErrorAction Stop
        return [string][int]$resp.StatusCode
    } catch {
        $r = $_.Exception.Response
        if ($r -and $r.StatusCode) { return [string][int]$r.StatusCode }
        return "000"  # connection failed / DNS / timeout
    }
}

function Set-RemoteEnv {
    param([string]$Embed, [string]$Rerank, [string]$ApiKey, [string]$Llm, [string]$LlmKey)
    # Exported into THIS process so `docker compose up` interpolates them into the service.
    $env:REMOTE_EMBED_URL  = $Embed
    $env:REMOTE_RERANK_URL = $Rerank
    $env:REMOTE_API_KEY    = $ApiKey
    $env:REMOTE_LLM_URL    = $Llm
    $env:REMOTE_LLM_KEY    = $LlmKey
}

switch ($Command) {
    "status" {
        Invoke-Status
    }

    "local" {
        Set-RemoteEnv -Embed "" -Rerank "" -ApiKey "" -Llm "" -LlmKey ""
        Write-Host "-> all model types LOCAL (CPU). NOTE: local embed = e5-small (384-d); if the index"
        Write-Host "   was built with a 1024-d model you must reembed.py or searches will dim-mismatch."
        Invoke-Recreate
    }

    "colab" {
        $embedUrl = ""; $embedKey = ""; $rerankUrl = ""; $llmUrl = ""; $llmKey = ""
        for ($i = 0; $i -lt $Rest.Count; $i++) {
            switch ($Rest[$i]) {
                "--embed-url"  { $embedUrl  = $Rest[++$i] }
                "--embed-key"  { $embedKey  = $Rest[++$i] }
                "--rerank-url" { $rerankUrl = $Rest[++$i] }
                "--llm-url"    { $llmUrl    = $Rest[++$i] }
                "--llm-key"    { $llmKey    = $Rest[++$i] }
                default        { [Console]::Error.WriteLine("unknown flag: $($Rest[$i])"); exit 2 }
            }
        }
        # rerank shares the embed tunnel unless overridden
        if (-not $rerankUrl) { $rerankUrl = $embedUrl }
        Set-RemoteEnv -Embed $embedUrl -Rerank $rerankUrl -ApiKey $embedKey -Llm $llmUrl -LlmKey $llmKey
        Write-Host "-> embed:  $(if ($embedUrl)  { $embedUrl }  else { 'LOCAL' })"
        Write-Host "-> rerank: $(if ($rerankUrl) { $rerankUrl } else { 'LOCAL' })"
        Write-Host "-> llm:    $(if ($llmUrl)    { $llmUrl }    else { 'LOCAL' })"
        Invoke-Recreate
    }

    default {
        [Console]::Error.WriteLine("usage: model-backend.ps1 {status|local|colab ...}")
        exit 2
    }
}
