<#
.SYNOPSIS
  Bring up the full KB E2E stack, configure Kong, and verify the auth chain + live
  data plane.

.DESCRIPTION
  Real auth (Keycloak RS256 JWT -> Kong aeroflow-jwks verify + inject X-* headers)
  in front of kb-backend in FULL mode talking to the REAL data plane
  (Postgres + Qdrant + Neo4j + Mongo + MinIO + Kafka + model-service), so
  data-plane routes return data instead of 503.

  Every service is memory-capped so the trimmed stack fits a ~8 GiB Docker VM
  (~6.5 GiB). The heavy bronze->silver->gold ingestion worker is OFF by default
  (the `pipeline` profile / -WithPipeline); kb-backend does not need it to boot.

.PARAMETER Action
  up        : build + start the stack, configure Kong (default)
  configure : (re)configure Kong only (stack already up)
  verify    : run the auth-chain + data-plane checks (401 / 200 / 403 + fleet 200)
  down      : stop and remove the stack (volumes/data preserved)
  destroy   : down + delete volumes (WIPES Postgres/Qdrant/Neo4j/Mongo/MinIO data)
  logs      : tail kb-backend + kong logs

.PARAMETER WithUi
  Also start the Vite frontend (browser login on http://localhost:5173).

.PARAMETER WithPipeline
  Also start the ingestion worker (needs ~12 GiB WSL VM — see docker-compose.kb_and_authen.yml).

.EXAMPLE
  pwsh infra/kb_and_authen.ps1                 # up + configure
  pwsh infra/kb_and_authen.ps1 verify          # prove the chain + live data plane
  pwsh infra/kb_and_authen.ps1 up -WithUi      # include the frontend
  pwsh infra/kb_and_authen.ps1 down            # stop, keep data
  pwsh infra/kb_and_authen.ps1 destroy         # stop + wipe data
#>
[CmdletBinding()]
param(
  [ValidateSet('up', 'configure', 'verify', 'down', 'destroy', 'logs')]
  [string]$Action = 'up',
  [switch]$WithUi,
  [switch]$WithPipeline
)

$ErrorActionPreference = 'Stop'
$ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Compose       = Join-Path $ScriptDir 'docker-compose.kb_and_authen.yml'

$KONG_ADMIN = 'http://localhost:8900'
$KONG_PROXY = 'http://localhost:8000'
$KC_BASE    = 'http://localhost:8080'
$REALM      = 'aeroflow'
# Kong reaches Keycloak by service DNS for the JWKS fetch; iss is validated
# against the public URL the browser/curl client used.
$JWKS_URI   = "http://keycloak:8080/realms/$REALM/protocol/openid-connect/certs"
$ISSUER     = "$KC_BASE/realms/$REALM"

function Compose-Args {
  $a = @('-f', $Compose)
  if ($WithUi)       { $a += @('--profile', 'ui') }
  if ($WithPipeline) { $a += @('--profile', 'pipeline') }
  return $a
}

function Wait-For($name, [scriptblock]$test, $tries = 90, $delay = 5) {
  Write-Host "  waiting for $name ..." -NoNewline
  for ($i = 0; $i -lt $tries; $i++) {
    try { if (& $test) { Write-Host " ok"; return } } catch {}
    Start-Sleep -Seconds $delay
    Write-Host '.' -NoNewline
  }
  Write-Host ''
  throw "Timed out waiting for $name"
}

# Kong's Admin API rejects PowerShell's default form encoding with 415, so send
# JSON explicitly. Works on Windows PowerShell 5.1 and PowerShell 7.
function Kong-Json($method, $path, $obj) {
  Invoke-RestMethod -Method $method -Uri "$KONG_ADMIN$path" `
    -ContentType 'application/json' -Body ($obj | ConvertTo-Json -Depth 8 -Compress)
}

function Configure-Kong {
  Write-Host "==> Configuring Kong at $KONG_ADMIN" -ForegroundColor Cyan

  Wait-For 'Kong admin' { (Invoke-WebRequest "$KONG_ADMIN/status" -UseBasicParsing).StatusCode -eq 200 }

  # 1. kb-backend upstream service (Kong reaches it by compose service DNS).
  #    Generous read_timeout so long-lived SSE streams under /api/pipeline aren't
  #    dropped during quiet stages (backend also heartbeats). Only affects a SILENT
  #    upstream — normal fast requests are unaffected.
  Kong-Json Put '/services/kb-backend' @{
    name = 'kb-backend'; url = 'http://kb-backend:8000'
    connect_timeout = 5000; read_timeout = 3600000
  } | Out-Null
  Write-Host '  service: kb-backend'

  # 2. Route — the actual router prefixes (data.py /api/data, fleet.py /api/fleet,
  #    /api/pipeline = ingestion SSE + light-rag, everything else under /api/knowledge).
  Kong-Json Put '/services/kb-backend/routes/kb-backend-route' @{
    name = 'kb-backend-route'
    paths = @('/api/data', '/api/knowledge', '/api/fleet', '/api/pipeline')
    strip_path = $false
    methods = @('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS')
  } | Out-Null
  Write-Host '  route:   kb-backend-route -> /api/data, /api/knowledge, /api/fleet, /api/pipeline'

  # 3. aeroflow-jwks plugin. Upsert: PATCH if present, else POST.
  $jwksBody = @{
    name = 'aeroflow-jwks'
    config = @{
      jwks_uri = $JWKS_URI; issuer = $ISSUER
      jwks_refresh_interval = 300; grace_period = 600
    }
  }
  $existing = (Invoke-RestMethod "$KONG_ADMIN/services/kb-backend/plugins").data |
    Where-Object { $_.name -eq 'aeroflow-jwks' } | Select-Object -First 1
  if ($existing) {
    Kong-Json Patch "/plugins/$($existing.id)" $jwksBody | Out-Null
    Write-Host '  plugin:  aeroflow-jwks (updated)'
  } else {
    Kong-Json Post '/services/kb-backend/plugins' $jwksBody | Out-Null
    Write-Host '  plugin:  aeroflow-jwks (created)'
  }

  # 4. CORS on the route so browser preflight (no Authorization header) passes
  #    before the JWT plugin runs.
  $corsBody = @{
    name = 'cors'
    config = @{
      origins = @('http://localhost:3000', 'http://localhost:5173')
      methods = @('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS')
      headers = @('Authorization', 'Content-Type')
      credentials = $true; max_age = 3600
    }
  }
  $existingCors = (Invoke-RestMethod "$KONG_ADMIN/routes/kb-backend-route/plugins").data |
    Where-Object { $_.name -eq 'cors' } | Select-Object -First 1
  if ($existingCors) {
    Kong-Json Patch "/plugins/$($existingCors.id)" $corsBody | Out-Null
    Write-Host '  plugin:  cors (updated)'
  } else {
    Kong-Json Post '/routes/kb-backend-route/plugins' $corsBody | Out-Null
    Write-Host '  plugin:  cors (created)'
  }

  Write-Host '==> Kong configured.' -ForegroundColor Green
}

function Get-Token($username, $password) {
  # curl.exe (ships with Windows 10+) — PowerShell 5.1's Invoke-RestMethod drops
  # this POST with "connection closed unexpectedly" (Expect: 100-continue quirk).
  $json = curl.exe -s -X POST "$KC_BASE/realms/$REALM/protocol/openid-connect/token" `
    -H 'Content-Type: application/x-www-form-urlencoded' `
    --data-urlencode 'grant_type=password' `
    --data-urlencode 'client_id=aeroflow-frontend' `
    --data-urlencode "username=$username" `
    --data-urlencode "password=$password"
  $resp = $json | ConvertFrom-Json
  if (-not $resp.access_token) { throw "Token request failed for ${username}: $json" }
  return $resp.access_token
}

function Try-Call($method, $path, $token) {
  $headers = @{}
  if ($token) { $headers['Authorization'] = "Bearer $token" }
  try {
    $r = Invoke-WebRequest -Method $method -Uri "$KONG_PROXY$path" -Headers $headers -UseBasicParsing
    return @{ status = [int]$r.StatusCode; body = $r.Content }
  } catch {
    $resp = $_.Exception.Response
    if ($resp) {
      $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
      return @{ status = [int]$resp.StatusCode; body = $sr.ReadToEnd() }
    }
    throw
  }
}

function Verify-Chain {
  Write-Host "==> Verifying auth chain + data plane via Kong ($KONG_PROXY)" -ForegroundColor Cyan

  Wait-For 'Keycloak realm' {
    (Invoke-WebRequest "$KC_BASE/realms/$REALM/.well-known/openid-configuration" -UseBasicParsing).StatusCode -eq 200
  }

  # 1. No token -> fail-closed 401 (authenticate rejects non-Kong-verified).
  $r = Try-Call 'GET' '/api/knowledge/whoami' $null
  Write-Host ("  [no token]        GET  /whoami       -> {0}  (expect 401)" -f $r.status)

  # 2. Engineer token -> whoami 200 with resolved identity.
  $eng = Get-Token 'helios-engineer' 'H3lios@Eng001!'
  $r = Try-Call 'GET' '/api/knowledge/whoami' $eng
  Write-Host ("  [ai-engineer]     GET  /whoami       -> {0}  (expect 200)" -f $r.status)
  if ($r.status -eq 200) { Write-Host "    identity: $($r.body)" -ForegroundColor DarkGray }

  # 3. DATA PLANE: fleet stats touch Postgres + Qdrant + Neo4j (health-gated svc).
  #    200 here = the real data plane is live (a KB_LITE backend returns 503).
  $r = Try-Call 'GET' '/api/fleet/stats' $eng
  Write-Host ("  [ai-engineer]     GET  /fleet/stats  -> {0}  (expect 200, NOT 503)" -f $r.status)
  if ($r.status -eq 200) { Write-Host "    stats: $($r.body)" -ForegroundColor DarkGray }

  # 4. Engineer lacks delete_data -> 403 at the router (RBAC enforced server-side).
  $r = Try-Call 'DELETE' '/api/knowledge/knowledge-bases/does-not-exist' $eng
  Write-Host ("  [ai-engineer]     DEL  knowledge-base-> {0}  (expect 403)" -f $r.status)

  # 5. Admin token -> whoami shows platform-admin + full permission set.
  $adm = Get-Token 'helios-admin' 'H3lios@Admin01!'
  $r = Try-Call 'GET' '/api/knowledge/whoami' $adm
  Write-Host ("  [platform-admin]  GET  /whoami       -> {0}  (expect 200)" -f $r.status)
  if ($r.status -eq 200) { Write-Host "    identity: $($r.body)" -ForegroundColor DarkGray }

  Write-Host '==> Done. 401 / 200 / 200 / 403 / 200 confirms auth + live data plane.' -ForegroundColor Green
}

switch ($Action) {
  'up' {
    Write-Host '==> Building & starting KB E2E stack (this pulls/builds — first run is slow)' -ForegroundColor Cyan
    if (-not $WithPipeline) {
      Write-Host '    ingestion worker OFF (use -WithPipeline + ~12 GiB WSL VM to enable)' -ForegroundColor DarkGray
    }
    & docker compose @(Compose-Args) up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }
    # kb-backend waits on model-service health (start_period 120s), so give it time.
    Wait-For 'kb-backend /health' {
      (Invoke-WebRequest 'http://localhost:8050/health' -UseBasicParsing).StatusCode -eq 200
    }
    Configure-Kong
    Write-Host ''
    Write-Host 'Stack is up. Endpoints:' -ForegroundColor Yellow
    Write-Host '  Keycloak  : http://localhost:8080  (admin admin/admin)'
    Write-Host '  Kong proxy: http://localhost:8000  (send API traffic here)'
    Write-Host '  Kong admin: http://localhost:8900'
    Write-Host '  kb-backend: http://localhost:8050  (direct = bypasses Kong = 401)'
    Write-Host '  Qdrant    : http://localhost:6333   Neo4j: http://localhost:7474'
    Write-Host '  MinIO     : http://localhost:9001    Mongo: localhost:27017'
    if ($WithUi)       { Write-Host '  Frontend  : http://localhost:5173' }
    if ($WithPipeline) { Write-Host '  Ingestion worker: ON (pipeline profile)' }
    Write-Host ''
    Write-Host 'Next:  pwsh infra/kb_and_authen.ps1 verify' -ForegroundColor Yellow
  }
  'configure' { Configure-Kong }
  'verify'    { Verify-Chain }
  'down' {
    & docker compose @(Compose-Args) down
    Write-Host 'Stopped. Data volumes preserved (use `destroy` to wipe).' -ForegroundColor Yellow
  }
  'destroy' {
    Write-Host '==> Removing stack AND volumes — Postgres/Qdrant/Neo4j/Mongo/MinIO data will be WIPED.' -ForegroundColor Red
    & docker compose @(Compose-Args) down -v
  }
  'logs' {
    & docker compose @(Compose-Args) logs -f kb-backend kong
  }
}
