#!/usr/bin/env bash
# docs/1.md "Kế hoạch 1" mục g — install gVisor (runsc) and register it as
# a Docker runtime, so pool.py can be pointed at it via SANDBOX_RUNTIME.
#
# NOT run automatically by anything in this repo, and intentionally not
# run as part of this change — installing a container runtime and editing
# the Docker daemon's config is a host-level action that can affect every
# other container on the same daemon if something goes wrong, so it's
# left as an explicit, reviewable operator step rather than something
# code silently does.
#
# Run this ON whatever Docker engine actually backs the sandbox pool
# containers (the host, or inside the rootless Docker-in-Docker sidecar —
# infra/docker-compose.app.yml's sandbox-dockerd, in this repo's compose
# setup — NOT on sandbox-executor itself, which never runs containers
# directly, only talks to the Docker API).
#
# This script only installs + registers the runtime. It does NOT set
# SANDBOX_RUNTIME=runsc for you, and you should not set it until you've
# done both remaining steps docs/1.md mục g calls out explicitly:
#   1. Syscall-compatibility pass — every ALLOWED_MODULES import
#      (pandas/numpy/plotly/matplotlib, security_visitor.py) must work
#      under runsc, especially their BLAS/native-extension paths.
#   2. Latency measurement — gVisor's syscall interception has real
#      overhead; measure a representative exec (import + compute + return)
#      under runsc vs. the default runtime and re-tune
#      SANDBOX_POOL_SIZE/timeouts if it eats meaningfully into the
#      latency win the container pool itself was built for.
set -euo pipefail

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) GVISOR_ARCH="x86_64" ;;
  aarch64) GVISOR_ARCH="aarch64" ;;
  *) echo "unsupported architecture for gVisor: $ARCH" >&2; exit 1 ;;
esac

RUNSC_PATH="${RUNSC_INSTALL_PATH:-/usr/local/bin/runsc}"
DAEMON_JSON="${DOCKER_DAEMON_JSON:-/etc/docker/daemon.json}"
BASE_URL="https://storage.googleapis.com/gvisor/releases/release/latest/${GVISOR_ARCH}"

echo "Downloading runsc (${GVISOR_ARCH}) to ${RUNSC_PATH}..."
curl -fsSL "${BASE_URL}/runsc" -o "${RUNSC_PATH}"
curl -fsSL "${BASE_URL}/runsc.sha512" -o /tmp/runsc.sha512
( cd "$(dirname "$RUNSC_PATH")" && sha512sum -c <(sed "s#runsc#$(basename "$RUNSC_PATH")#" /tmp/runsc.sha512) )
chmod +x "${RUNSC_PATH}"
rm -f /tmp/runsc.sha512

echo "Registering runsc in ${DAEMON_JSON}..."
python3 - "$DAEMON_JSON" "$RUNSC_PATH" <<'PYEOF'
import json
import pathlib
import sys

daemon_json_path, runsc_path = pathlib.Path(sys.argv[1]), sys.argv[2]
config = json.loads(daemon_json_path.read_text()) if daemon_json_path.exists() else {}
config.setdefault("runtimes", {})["runsc"] = {"path": runsc_path}
daemon_json_path.write_text(json.dumps(config, indent=2) + "\n")
print(f"updated {daemon_json_path}")
PYEOF

cat <<'EOF'

runsc installed and registered. Restart the Docker daemon to pick it up:
  systemctl restart docker                     # host dockerd
  # or, if using the rootless DinD sidecar (infra/docker-compose.app.yml):
  docker compose -f infra/docker-compose.app.yml restart sandbox-dockerd

Before setting SANDBOX_RUNTIME=runsc on sandbox-executor, run the two
remaining steps docs/1.md mục g requires (see this script's header):

  # 1) syscall-compatibility pass — every ALLOWED_MODULES import must work
  docker build -t sandbox-runner:latest services/sandbox-executor/sandbox_runner/
  docker run --rm --runtime=runsc sandbox-runner:latest python3 -c \
    "import pandas, numpy, plotly, matplotlib; print('ok')"

  # 2) latency comparison — representative exec under runsc vs. default
  time docker run --rm --runtime=runsc sandbox-runner:latest python3 -c \
    "import pandas as pd; print(pd.DataFrame({'a':[1,2,3]}).sum().to_dict())"
  time docker run --rm sandbox-runner:latest python3 -c \
    "import pandas as pd; print(pd.DataFrame({'a':[1,2,3]}).sum().to_dict())"

Only after both pass: set SANDBOX_RUNTIME=runsc on sandbox-executor and restart it.
EOF
