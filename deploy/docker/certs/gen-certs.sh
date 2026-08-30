#!/usr/bin/env bash
# ─── mTLS Certificate Generator ──────────────────────────────────────
# Generates a self-signed CA + Kong client cert for Kong → Backend mTLS.
# Kong presents this client cert on every upstream request.
# Backend validates it against ca.crt to ensure requests come from Kong.
#
# Usage:
#   bash deploy/docker/certs/gen-certs.sh
#
# Output (git-ignored):
#   deploy/docker/certs/ca.crt          ← CA cert (share with backend)
#   deploy/docker/certs/ca.key          ← CA private key — store offline in production
#   deploy/docker/certs/kong-client.crt ← Kong's client cert
#   deploy/docker/certs/kong-client.key ← Kong's client key (keep secret)
#   deploy/docker/certs/backend.crt     ← Placeholder backend server cert
#   deploy/docker/certs/backend.key     ← Placeholder backend server key
#
# ⚠️  ROTATION / REVOCATION STRATEGY (production):
#   - CA:           10 years — rotate offline; store ca.key in a secrets vault (e.g. HashiCorp Vault).
#   - kong-client:  825 days — set a calendar reminder; re-run this script + update Kong cert.
#   - backend:      825 days — same cadence; update TLS config in backend service.
#   For revocation, maintain a CRL or use OCSP. In Kubernetes, use cert-manager for automation.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

command -v openssl > /dev/null || { echo "❌ openssl not found"; exit 1; }

# Protect all private keys at creation time
umask 077

echo "🔑 Generating mTLS certificates for AeroFlow..."

# ── CA ─────────────────────────────────────────────────────────────────
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=AeroFlow-CA/O=AeroFlow/C=VN" 2>/dev/null
chmod 600 ca.key
echo "  → CA:            ca.crt (valid 10 years)"
echo "  ⚠️  ca.key must NOT remain alongside runtime certs in production."
echo "     Move it to an offline secrets vault after issuing leaf certs."

# ── Kong client cert (.ext enforces clientAuth EKU) ───────────────────
cat > kong-client.ext <<'EOF'
basicConstraints = CA:FALSE
keyUsage = digitalSignature
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

openssl genrsa -out kong-client.key 2048 2>/dev/null
openssl req -new -key kong-client.key -out kong-client.csr \
  -subj "/CN=kong-gateway/O=AeroFlow/C=VN" 2>/dev/null
openssl x509 -req -days 825 -in kong-client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -extfile kong-client.ext \
  -out kong-client.crt 2>/dev/null
chmod 600 kong-client.key
rm -f kong-client.csr kong-client.ext ca.srl
echo "  → Kong client:   kong-client.crt / kong-client.key (clientAuth EKU)"

# ── workflow_runtime client cert ──────────────────────────────────────
# Second client identity under the same CA. workflow_runtime calls kb-backend
# directly (kb_search -> /api/pipeline/light-rag/retrieve), not through Kong,
# so it needs its own cert rather than sharing Kong's: distinct subjects mean
# an upstream can tell the two callers apart, and either can be revoked
# without disturbing the other.
cat > workflow_runtime-client.ext <<'EOF'
basicConstraints = CA:FALSE
keyUsage = digitalSignature
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

openssl genrsa -out workflow_runtime-client.key 2048 2>/dev/null
openssl req -new -key workflow_runtime-client.key -out workflow_runtime-client.csr \
  -subj "/CN=workflow_runtime/O=AeroFlow/C=VN" 2>/dev/null
openssl x509 -req -days 825 -in workflow_runtime-client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -extfile workflow_runtime-client.ext \
  -out workflow_runtime-client.crt 2>/dev/null
chmod 600 workflow_runtime-client.key
rm -f workflow_runtime-client.csr workflow_runtime-client.ext ca.srl
echo "  → WF client:     workflow_runtime-client.crt / .key (clientAuth EKU)"

# ── Backend server cert (.ext enforces serverAuth + SAN) ──────────────
# Modern TLS clients ignore CN; SAN is required for hostname verification.
cat > backend.ext <<'EOF'
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:aeroflow-backend, DNS:localhost, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

openssl genrsa -out backend.key 2048 2>/dev/null
openssl req -new -key backend.key -out backend.csr \
  -subj "/CN=aeroflow-backend/O=AeroFlow/C=VN" 2>/dev/null
openssl x509 -req -days 825 -in backend.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -extfile backend.ext \
  -out backend.crt 2>/dev/null
chmod 600 backend.key
rm -f backend.csr backend.ext ca.srl 2>/dev/null || true
echo "  → Backend server: backend.crt / backend.key (serverAuth + SAN)"

# ── Verify chain ───────────────────────────────────────────────────────
openssl verify -CAfile ca.crt kong-client.crt > /dev/null && \
  echo "  → Chain verified: kong-client.crt ✓"
openssl verify -CAfile ca.crt workflow_runtime-client.crt > /dev/null && \
  echo "  → Chain verified: workflow_runtime-client.crt ✓"
openssl verify -CAfile ca.crt backend.crt > /dev/null && \
  echo "  → Chain verified: backend.crt ✓"

echo ""
echo "✅ Certificates generated in deploy/docker/certs/"
echo ""
echo "Next step — register Kong client cert with Kong Admin API:"
echo "  bash deploy/docker/kong/kong-setup.sh"
echo "(kong-setup.sh will pick up deploy/docker/certs/kong-client.crt automatically)"
echo ""
echo "⚠️  Keep ca.key, kong-client.key, and backend.key secret — never commit to git."
echo "⚠️  In production, move ca.key to an offline vault immediately after cert issuance."
echo "⚠️  Schedule cert renewal before expiry (825 days from today)."
