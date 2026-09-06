#!/usr/bin/env bash
# Alerts when / crosses 80% used. Host-level, not a k8s/Prometheus rule —
# see the note this was introduced with: local-path-provisioner doesn't
# enforce PVC size as a real quota (deploy/k8s/database/*.yaml's `storage:`
# values are advisory), so the actual risk is /, not any one PVC, and node
# disk metrics aren't in Prometheus here (no node-exporter/cadvisor scrape
# target — adding one needs either a hostPath volume, which the
# `restricted` PodSecurity namespaces forbid, or kubelet's own cadvisor
# endpoint + cluster-scoped RBAC, neither wired up (yet) to keep this a
# same-day fix rather than a new architecture piece). This script is the
# stopgap: it works today, with nothing new to deploy.
#
# Alerts to: syslog (journalctl -t disk-alert) + a dedicated log file, so
# there's always somewhere to see it even with no chat/email configured.
# Wire in a real channel by editing notify() below (a `curl` to a Slack
# webhook, `mail`, etc.) once one exists for this project.
#
# Install once:  crontab -e
#   */15 * * * * /home/ndip/Data-Agent/deploy/k8s/ops/disk-alert.sh
set -euo pipefail

THRESHOLD=80
MOUNT=/
LOG=/home/ndip/data-agent-registry-backups/disk-alert.log
STATE=/tmp/.disk-alert-last-state   # avoids re-logging every 15 min once already over threshold

mkdir -p "$(dirname "$LOG")"

usage=$(df --output=pcent "$MOUNT" | tail -1 | tr -dc '0-9')

notify() {
  local msg="$1"
  logger -t disk-alert -p user.warning "$msg"
  echo "[$(date -Iseconds)] $msg" >> "$LOG"
}

was_over=0
[ -f "$STATE" ] && was_over=$(cat "$STATE")

if [ "$usage" -ge "$THRESHOLD" ]; then
  if [ "$was_over" -eq 0 ]; then
    notify "ALERT: $MOUNT is ${usage}% full (threshold ${THRESHOLD}%). df -h / ; docker system df ; du -sh /var/lib/rancher/k3s/storage/* 2>/dev/null | sort -rh | head to find what to reclaim."
  fi
  echo 1 > "$STATE"
else
  if [ "$was_over" -eq 1 ]; then
    notify "RESOLVED: $MOUNT back down to ${usage}% (threshold ${THRESHOLD}%)."
  fi
  echo 0 > "$STATE"
fi
