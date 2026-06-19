# --- mTLS Certificate Generator (Windows / PowerShell) ---------------
# PowerShell twin of gen-certs.sh - same output, no Git Bash required.
# Generates a self-signed CA + Kong client cert for Kong - Backend mTLS.
# Kong presents this client cert on every upstream request.
# Backend validates it against ca.crt to ensure requests come from Kong.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File infra/certs/gen-certs.ps1
#
# Requires openssl on PATH (Git for Windows ships one under its usr\bin).
#
# Output (git-ignored):
#   infra/certs/ca.crt          - CA cert (share with backend)
#   infra/certs/ca.key          - CA private key - store offline in production
#   infra/certs/kong-client.crt - Kong's client cert
#   infra/certs/kong-client.key - Kong's client key (keep secret)
#   infra/certs/backend.crt     - Placeholder backend server cert
#   infra/certs/backend.key     - Placeholder backend server key
#
# ROTATION / REVOCATION STRATEGY (production): see gen-certs.sh header.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    Write-Error "openssl not found. Install Git for Windows (ships openssl) or OpenSSL and add it to PATH."
    exit 1
}

function Invoke-OpenSSL {
    # openssl writes informational lines to stderr (e.g. "Certificate request
    # self-signature ok"). In PS 5.1, redirecting native stderr while EAP=Stop
    # turns those into a terminating error, so soften EAP locally (function
    # scope, auto-restored) and rely on the exit code instead.
    $ErrorActionPreference = 'SilentlyContinue'
    & openssl @args 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "openssl $($args -join ' ') failed (exit $LASTEXITCODE)"
    }
}

function Protect-KeyFile {
    param([string]$Path)
    # No chmod on Windows - best-effort: drop inheritance, grant only this user.
    try {
        icacls $Path /inheritance:r /grant:r "${env:USERNAME}:(R,W)" *> $null
    } catch {
        Write-Warning "Could not tighten ACL on $Path - keep it out of source control manually."
    }
}

Write-Host "Generating mTLS certificates for AeroFlow..." -ForegroundColor Cyan

# -- CA -----------------------------------------------------------------
Invoke-OpenSSL genrsa -out ca.key 4096
Invoke-OpenSSL req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=AeroFlow-CA/O=AeroFlow/C=VN"
Protect-KeyFile ca.key
Write-Host "  -> CA:            ca.crt (valid 10 years)"
Write-Host "  !  ca.key must NOT remain alongside runtime certs in production." -ForegroundColor Yellow
Write-Host "     Move it to an offline secrets vault after issuing leaf certs."

# -- Kong client cert (.ext enforces clientAuth EKU) -------------------
@'
basicConstraints = CA:FALSE
keyUsage = digitalSignature
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
'@ | Set-Content -Path kong-client.ext -Encoding ascii

Invoke-OpenSSL genrsa -out kong-client.key 2048
Invoke-OpenSSL req -new -key kong-client.key -out kong-client.csr -subj "/CN=kong-gateway/O=AeroFlow/C=VN"
Invoke-OpenSSL x509 -req -days 825 -in kong-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -extfile kong-client.ext -out kong-client.crt
Protect-KeyFile kong-client.key
Remove-Item kong-client.csr, kong-client.ext, ca.srl -ErrorAction SilentlyContinue
Write-Host "  -> Kong client:   kong-client.crt / kong-client.key (clientAuth EKU)"

# -- Backend server cert (.ext enforces serverAuth + SAN) --------------
# Modern TLS clients ignore CN; SAN is required for hostname verification.
@'
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:aeroflow-backend, DNS:localhost, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
'@ | Set-Content -Path backend.ext -Encoding ascii

Invoke-OpenSSL genrsa -out backend.key 2048
Invoke-OpenSSL req -new -key backend.key -out backend.csr -subj "/CN=aeroflow-backend/O=AeroFlow/C=VN"
Invoke-OpenSSL x509 -req -days 825 -in backend.csr -CA ca.crt -CAkey ca.key -CAcreateserial -extfile backend.ext -out backend.crt
Protect-KeyFile backend.key
Remove-Item backend.csr, backend.ext, ca.srl -ErrorAction SilentlyContinue
Write-Host "  -> Backend server: backend.crt / backend.key (serverAuth + SAN)"

# -- Verify chain -------------------------------------------------------
Invoke-OpenSSL verify -CAfile ca.crt kong-client.crt | Out-Null
Write-Host "  -> Chain verified: kong-client.crt OK"
Invoke-OpenSSL verify -CAfile ca.crt backend.crt | Out-Null
Write-Host "  -> Chain verified: backend.crt OK"

Write-Host ""
Write-Host "Certificates generated in infra/certs/" -ForegroundColor Green
Write-Host ""
Write-Host "Next step - register Kong client cert with Kong Admin API (runs inside the"
Write-Host "kong-setup container, no host shell needed):"
Write-Host "  docker compose --profile setup run --rm kong-setup"
Write-Host ""
Write-Host "!  Keep ca.key, kong-client.key, and backend.key secret - never commit to git." -ForegroundColor Yellow
Write-Host "!  In production, move ca.key to an offline vault immediately after cert issuance." -ForegroundColor Yellow
Write-Host "!  Schedule cert renewal before expiry (825 days from today)." -ForegroundColor Yellow
