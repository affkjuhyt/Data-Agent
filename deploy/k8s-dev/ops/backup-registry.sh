#!/usr/bin/env bash
# Daily backup of the local Docker registry (deploy/k8s/README.md's
# build-push-images.sh target — losing this means rebuilding all 16 images
# from source before anything in the cluster can be rescheduled). The
# registry now bind-mounts a real host directory
# (/home/ndip/data-agent-registry, see deploy/k8s/README.md or the session
# that set this up) instead of an anonymous Docker volume specifically so
# this is possible without stopping the container.
#
# Install once:  crontab -e
#   0 3 * * * /home/ndip/Data-Agent/deploy/k8s/ops/backup-registry.sh >> /home/ndip/data-agent-registry-backups/backup.log 2>&1
set -euo pipefail

SRC=/home/ndip/data-agent-registry
DEST_DIR=/home/ndip/data-agent-registry-backups
KEEP=7

mkdir -p "$DEST_DIR"
ts=$(date +%Y%m%d_%H%M%S)
dest="$DEST_DIR/registry_${ts}.tar.gz"

tar -czf "$dest" -C "$(dirname "$SRC")" "$(basename "$SRC")"
echo "[$(date -Iseconds)] backed up $SRC -> $dest ($(du -h "$dest" | cut -f1))"

# Keep the last $KEEP backups only.
ls -1t "$DEST_DIR"/registry_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
