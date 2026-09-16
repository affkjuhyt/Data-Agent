#!/usr/bin/env bash
# Daily backup of the local Docker registry (deploy/k8s/README.md's
# build-push-images.sh target — losing this means rebuilding all 16 images
# from source before anything in the cluster can be rescheduled). The
# registry now bind-mounts a real host directory
# (/home/ndip/data-agent-registry, see deploy/k8s/README.md or the session
# that set this up) instead of an anonymous Docker volume specifically so
# this is possible without stopping the container.
#
# Hardlinked snapshots rather than a nightly tar.gz. A registry stores
# content-addressed, immutable, ALREADY-gzipped layer blobs, so the old
# `tar -czf` spent ~6m19s of single-threaded gzip to shrink 9.896.198.219
# bytes to 9.827.931.497 — 0,7% — and wrote a fresh 9 GB every night, three
# of which outgrew every other consumer on /. rsync --link-dest instead
# copies only blobs that are new since the previous snapshot and hardlinks
# the rest, so each extra snapshot costs roughly its own delta on disk and
# seconds of wall time.
#
# Restore:  rsync -aHAX --delete <snapshot>/ /home/ndip/data-agent-registry/
#           (stop the registry container first)
#
# Install once:  crontab -e
#   0 3 * * * /home/ndip/Data-Agent/deploy/k8s/ops/backup-registry.sh >> /home/ndip/data-agent-registry-backups/backup.log 2>&1
set -euo pipefail

SRC=/home/ndip/data-agent-registry
# On /data (the 1 TB disk), not under $HOME on the 163 GB root: three archives
# of a registry that grew 2.8G -> 9.2G in ten days kept disk-alert.sh firing.
# Only the snapshots moved — backup.log and disk-alert.log still live beside
# $SRC, so the crontab lines that redirect into them need no change.
DEST_DIR=/data/registry-backups
KEEP=3

mkdir -p "$DEST_DIR"
dest="$DEST_DIR/registry_$(date +%Y%m%d_%H%M%S)"
staging="$dest.partial"

# Hardlink unchanged blobs against the newest COMPLETED snapshot. A run that
# dies midway leaves only *.partial, which this excludes and the trap removes —
# the previous scheme left a truncated .tar.gz that looked like a valid backup
# and, being newer, evicted a good one under the retention rule below.
prev=$(find "$DEST_DIR" -maxdepth 1 -type d -name 'registry_*' ! -name '*.partial' | sort | tail -1)
link_dest=()
[ -n "$prev" ] && link_dest=(--link-dest="$prev")

rm -rf "$staging"
trap 'rm -rf "$staging"' EXIT
rsync -aHAX --delete "${link_dest[@]}" "$SRC/" "$staging/"
mv "$staging" "$dest"
trap - EXIT

echo "[$(date -Iseconds)] backed up $SRC -> $dest" \
     "($(du -sh "$dest" | cut -f1) logical," \
     "$(du -sh "$DEST_DIR" | cut -f1) on disk for all snapshots)"

# Keep the last $KEEP snapshots only.
find "$DEST_DIR" -maxdepth 1 -type d -name 'registry_*' ! -name '*.partial' \
  | sort -r | tail -n +$((KEEP + 1)) | xargs -r rm -rf
