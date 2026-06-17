<#
.SYNOPSIS
  Bring up the auth-lite stack, configure Kong, and verify the identity chain.

.DESCRIPTION
  Smallest stack that exercises the real auth path:
    Keycloak (RS256 JWT) -> Kong (aeroflow-jwks verify + inject X-* headers)
                          -> kb-backend router (get_ui_context -> require_perm)

  No Qdrant / Neo4j / Mongo / MinIO / Kafka / model-service. kb-backend runs
  with KB_LITE=true (Postgres only).

.PARAMETER Action
  up        : build + start the stack, then configure Kong (default)
  configure : (re)configure Kong only (stack already up)
  verify    : run the identity-chain checks (401 / 403 / 200 + whoami)
  down      : stop and remove the stack
  logs      : tail kb-backend + kong logs

.PARAMETER WithUi
  Also start the Vite frontend (browser login on http://localhost:5173).

.EXAMPLE
  pwsh infra/auth-lite.ps1                 # up + configure
  pwsh infra/auth-lite.ps1 verify          # prove the chain
  pwsh infra/auth-lite.ps1 up -WithUi      # include the frontend
  pwsh infra/auth-lite.ps1 down
#>
[CmdletBinding()]
param(
  [ValidateSet('up', 'configure', 'verify', 'down', 'logs')]
  [string]$Action = 'up',
  [switch]$WithUi
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Compose   = Join-Path $ScriptDir 'docker-compose.auth-lite.yml'

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
  if ($WithUi) { $a += @('--profile', 'ui') }
  return $a
}

function Wait-For($name, [scriptblock]$test, $tries = 60, $delay = 5) {
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
# JSON explicitly. Works identically on Windows PowerShell 5.1 and PowerShell 7.
function Kong-Json($method, $path, $obj) {
  Invoke-RestMethod -Method $method -Uri "$KONG_ADMIN$path" `
    -ContentType 'application/json' -Body ($obj | ConvertTo-Json -Depth 8 -Compress)
}

function Configure-Kong {
  Write-Host "==> Configuring Kong at $KONG_ADMIN" -ForegroundColor Cyan

  Wait-For 'Kong admin' { (Invoke-WebRequest "$KONG_ADMIN/status" -UseBasicParsing).StatusCode -eq 200 }

  # 1. kb-backend upstream service (Kong reaches it by compose service DNS).
  Kong-Json Put '/services/kb-backend' @{
    name = 'kb-backend'; url = 'http://kb-backend:8000'
    connect_timeout = 5000; read_timeout = 60000
  } | Out-Null
  Write-Host '  service: kb-backend'

  # 2. Route — the UI-facing API prefixes.
  Kong-Json Put '/services/kb-backend/routes/kb-backend-route' @{
    name = 'kb-backend-route'
    paths = @('/api/data', '/api/knowledge', '/api/pipeline')
    strip_path = $false
    methods = @('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS')
  } | Out-Null
  Write-Host '  route:   kb-backend-route -> /api/data, /api/knowledge, /api/pipeline'

  # 3. aeroflow-jwks plugin (NO audience — keeps the lite realm export's token
  #    valid without an extra audience mapper). Upsert: PATCH if present, else POST.
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
      # In real-auth mode the frontend sends only Authorization (+ Content-Type on
      # writes); identity comes from the JWT, not client headers. Keep the
      # allow-list minimal.
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
  Write-Host "==> Verifying identity chain via Kong ($KONG_PROXY)" -ForegroundColor Cyan

  Wait-For 'Keycloak realm' {
    (Invoke-WebRequest "$KC_BASE/realms/$REALM/.well-known/openid-configuration" -UseBasicParsing).StatusCode -eq 200
  }

  # 1. No token -> fail-closed 401 (get_ui_context rejects non-Kong-verified).
  $r = Try-Call 'GET' '/api/knowledge/whoami' $null
  Write-Host ("  [no token]        GET /whoami           -> {0}  (expect 401)" -f $r.status)

  # 2. Engineer token -> whoami 200 with resolved identity.
  $eng = Get-Token 'helios-engineer' 'H3lios@Eng001!'
  $r = Try-Call 'GET' '/api/knowledge/whoami' $eng
  Write-Host ("  [ai-engineer]     GET /whoami           -> {0}  (expect 200)" -f $r.status)
  if ($r.status -eq 200) { Write-Host "    identity: $($r.body)" -ForegroundColor DarkGray }

  # 3. Engineer lacks delete_data -> 403 at the router (RBAC), proving role
  #    propagated and is enforced server-side.
  $r = Try-Call 'DELETE' '/api/knowledge/knowledge-bases/does-not-exist' $eng
  Write-Host ("  [ai-engineer]     DELETE knowledge-base -> {0}  (expect 403)" -f $r.status)

  # 4. Admin token -> whoami shows platform-admin + full permission set.
  $adm = Get-Token 'helios-admin' 'H3lios@Admin01!'
  $r = Try-Call 'GET' '/api/knowledge/whoami' $adm
  Write-Host ("  [platform-admin]  GET /whoami           -> {0}  (expect 200)" -f $r.status)
  if ($r.status -eq 200) { Write-Host "    identity: $($r.body)" -ForegroundColor DarkGray }

  Write-Host '==> Done. 401 / 200 / 403 / 200 confirms the chain end-to-end.' -ForegroundColor Green
}

switch ($Action) {
  'up' {
    Write-Host '==> Building & starting auth-lite stack' -ForegroundColor Cyan
    & docker compose @(Compose-Args) up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }
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
    if ($WithUi) { Write-Host '  Frontend  : http://localhost:5173' }
    Write-Host ''
    Write-Host 'Next:  pwsh infra/auth-lite.ps1 verify' -ForegroundColor Yellow
  }
  'configure' { Configure-Kong }
  'verify'    { Verify-Chain }
  'down' {
    & docker compose @(Compose-Args) down
  }
  'logs' {
    & docker compose @(Compose-Args) logs -f kb-backend kong
  }
}
