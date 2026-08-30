#!/usr/bin/env bash
# backend/services/sandbox-executor/ops/ "Kế hoạch 1" mục a — DOCKER-USER iptables rules for the
# `sandbox-egress` bridge network that pool.py creates (subnet
# 172.28.0.0/24 by default, SANDBOX_EGRESS_SUBNET) when
# SANDBOX_EGRESS_ENABLED=true.
#
# Run this on whatever Docker engine actually backs the sandbox pool
# containers:
#   - if sandbox-executor talks to the host's own /var/run/docker.sock
#     (SANDBOX_DOCKER_HOST unset): run directly on the host.
#   - if using the rootless Docker-in-Docker sidecar
#     (deploy/docker/docker-compose.app.yml's sandbox-dockerd,
#     SANDBOX_DOCKER_HOST=tcp://sandbox-dockerd:2375, the default in this
#     repo's compose setup): run INSIDE that sidecar's own network
#     namespace, e.g. `docker exec -it dataagent-sandbox-dockerd sh -c '...'`
#     (needs iptables available in that image) or via `nsenter` into its
#     netns from the host — DOCKER-USER on the outer host's dockerd does
#     NOT see traffic from containers running inside the inner rootless
#     dockerd.
#
# Run the network into existence first — start sandbox-executor once with
# SANDBOX_EGRESS_ENABLED=true (pool.py creates the network on startup), or
# `docker network create` it yourself with the same driver/subnet/options
# pool.py uses (see pool.py's _ensure_egress_network).
#
# Idempotent: safe to re-run after changing SNOWFLAKE_PRIVATELINK_CIDRS or
# SANDBOX_DNS_RESOLVER — old rules from a previous run of this script are
# removed by comment marker before the new ones are inserted, so re-running
# cleanly replaces rather than stacking duplicates.
#
# NOT applied automatically by any code in this repo. pool.py's own
# startup probe (_verify_egress_isolation) checks whether default-deny is
# actually in effect and falls the whole pool back to no network at all
# if it isn't — so forgetting to run this script fails safe (no egress at
# all), not open.
set -euo pipefail

SUBNET="${SANDBOX_EGRESS_SUBNET:-172.28.0.0/24}"
CHAIN="DOCKER-USER"
MARKER="sandbox-egress"

# Snowflake PrivateLink CIDR(s) for the warehouse(s) this pool is allowed
# to reach — comma-separated, e.g. "10.10.20.0/28,10.10.21.0/28". Must be
# the tenant's actual PrivateLink CIDR (each warehouse has one fixed
# internal VPC IP, per backend/services/sandbox-executor/ops/ mục a) — never a public Snowflake
# endpoint range, never a wildcard hostname. No default: empty means
# nothing is allowed through, i.e. still deny-all.
SNOWFLAKE_PRIVATELINK_CIDRS="${SNOWFLAKE_PRIVATELINK_CIDRS:-}"

# Internal DNS resolver that only answers for the whitelisted Snowflake
# hostnames (each resolving to a PrivateLink IP above, never a public
# one) — set per environment, no default.
SANDBOX_DNS_RESOLVER="${SANDBOX_DNS_RESOLVER:-}"

if ! command -v iptables >/dev/null 2>&1; then
  echo "iptables not found on this host/container — run this script where DOCKER-USER actually lives (see header comment)" >&2
  exit 1
fi

echo "sandbox-egress iptables setup: subnet=$SUBNET privatelink_cidrs=[$SNOWFLAKE_PRIVATELINK_CIDRS] dns_resolver=[$SANDBOX_DNS_RESOLVER]"

# Remove this script's own previously-inserted rules (idempotent re-run),
# matched by comment marker rather than rule content.
while rule_num=$(iptables -L "$CHAIN" --line-numbers -n 2>/dev/null | awk -v m="$MARKER" '$0 ~ m {print $1; exit}'); do
  [ -z "$rule_num" ] && break
  iptables -D "$CHAIN" "$rule_num"
done

# Order matters — iptables evaluates top-down within a chain, first
# matching rule wins, so the specific ACCEPTs must be inserted above the
# final catch-all DROP.

# 1) Hard-block the cloud metadata endpoint first, before anything else
#    could allow it by accident (doc mục a: "Chặn cứng endpoint cloud
#    metadata — chống SSRF").
iptables -I "$CHAIN" 1 -s "$SUBNET" -d 169.254.169.254 -j DROP \
  -m comment --comment "$MARKER: block cloud metadata"

# 2) Allow DNS only to the designated resolver (skipped if unset — no
#    DNS at all is a safe, if inconvenient, default).
if [ -n "$SANDBOX_DNS_RESOLVER" ]; then
  iptables -I "$CHAIN" 2 -s "$SUBNET" -d "$SANDBOX_DNS_RESOLVER" -p udp --dport 53 -j ACCEPT \
    -m comment --comment "$MARKER: dns udp"
  iptables -I "$CHAIN" 3 -s "$SUBNET" -d "$SANDBOX_DNS_RESOLVER" -p tcp --dport 53 -j ACCEPT \
    -m comment --comment "$MARKER: dns tcp"
fi

# 3) Allow HTTPS only to the configured Snowflake PrivateLink CIDR(s)
#    (skipped if unset — nothing is allowed, still deny-all).
if [ -n "$SNOWFLAKE_PRIVATELINK_CIDRS" ]; then
  IFS=',' read -ra CIDRS <<< "$SNOWFLAKE_PRIVATELINK_CIDRS"
  for cidr in "${CIDRS[@]}"; do
    iptables -I "$CHAIN" -s "$SUBNET" -d "$cidr" -p tcp --dport 443 -j ACCEPT \
      -m comment --comment "$MARKER: snowflake $cidr"
  done
fi

# 4) Default deny — anything from this subnet not explicitly allowed
#    above is dropped. Appended (not inserted), so it's evaluated last.
iptables -A "$CHAIN" -s "$SUBNET" -j DROP -m comment --comment "$MARKER: default deny"

echo "Done. Verify: iptables -L $CHAIN -n --line-numbers | grep $MARKER"
echo "sandbox-executor also self-checks this automatically on every startup"
echo "(pool.py's _verify_egress_isolation — see its log line 'egress isolation')."
