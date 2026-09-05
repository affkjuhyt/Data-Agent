# OpenBao production config — TLS enabled.
# Selected with OPENBAO_CONFIG_FILE=config.prod.hcl; openbao.yml mounts ./certs at /openbao/certs
# and runs `server -config=/openbao/config/config.prod.hcl`.
#
# Generate the cert/key first (self-signed for staging, real CA for prod):
#   bash deploy/docker/certs/gen-certs.sh          # or gen-certs.ps1 on Windows
# and ensure it emits openbao.crt / openbao.key into deploy/docker/certs/.
storage "file" {
  path = "/openbao/data"
}

listener "tcp" {
  address         = "0.0.0.0:8200"
  tls_cert_file   = "/openbao/certs/openbao.crt"
  tls_key_file    = "/openbao/certs/openbao.key"
  tls_min_version = "tls12"
}

api_addr = "https://openbao:8200"
ui       = true
