"""Restore drill (PL-04): the latest backups restored into throwaway pods, compared with the live stores.

    python3 tests/ops/restore_drill.py [--namespace dataagent-service] [--keep]

`make restore-drill` runs it against the current kubectl context. Nothing live is written: each
store is restored into a pod of its own (label app=restore-drill) that mounts the backup volume
read-only, and the pods are deleted at the end (--keep leaves them for a look). What it proves is
that the files the CronJobs keep (RUNBOOK §9a) restore, and how long a restore takes:

- PostgreSQL: the roles, then every `dump_<db>_<ts>` of the latest run into a fresh server;
  every table of the live database must exist in the restored one. Row counts are compared and
  reported; the live side has kept writing since the dump, so a difference is listed, not failed.
- MinIO: the mirror loaded into a fresh MinIO; per bucket, the restored objects must be at least
  the live ones (the mirror keeps deleted objects, see §9a).
- Qdrant: each collection snapshot uploaded into a fresh Qdrant; its points must equal the live
  collection's.

OpenBao is not covered: it has no backup yet (§9a). Standard library only; it drives kubectl.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time

DRILL = "restore-drill"
PG_IMAGE = "postgres:16-alpine"
MINIO_IMAGE = "bitnamilegacy/minio:2024.12.18-debian-12-r0"
QDRANT_IMAGE = "qdrant/qdrant:v1.13.6"
LIVE_DB = ("dataagent-database", "postgres-0")
LIVE_QDRANT = "http://qdrant.dataagent-database.svc.cluster.local:8330"
# Exact row counts of every base table, one line per table: schema.table<TAB>rows.
COUNTS_SQL = """
SELECT table_schema || '.' || table_name || chr(9) ||
       (xpath('/row/c/text()', query_to_xml(
           format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1
"""


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    done = subprocess.run(
        ["kubectl", *args], input=stdin, capture_output=True, text=True, check=False
    )
    if check and done.returncode != 0:
        raise SystemExit(f"kubectl {' '.join(args[:4])}…: {done.stderr.strip()[-400:]}")
    return done.stdout


def pod(
    ns: str,
    name: str,
    image: str,
    command: list[str],
    env: dict[str, str],
    volume: str | None,
    user: int | None = None,
) -> dict:
    mounts = [{"name": "data", "mountPath": "/drill"}]
    volumes: list[dict] = [{"name": "data", "emptyDir": {}}]
    if volume:
        mounts.append({"name": "backups", "mountPath": "/backups", "readOnly": True})
        volumes.append(
            {"name": "backups", "persistentVolumeClaim": {"claimName": volume, "readOnly": True}}
        )
    spec: dict = {
        "restartPolicy": "Never",
        "enableServiceLinks": False,
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "drill",
                "image": image,
                "command": command,
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "volumeMounts": mounts,
            }
        ],
        "volumes": volumes,
    }
    if user is not None:
        spec["securityContext"] = {"runAsUser": user, "runAsGroup": user, "fsGroup": user}
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": ns, "labels": {"app": DRILL, "drill": name}},
        "spec": spec,
    }


def start(ns: str, manifest: dict) -> None:
    kubectl("apply", "-f", "-", stdin=json.dumps(manifest))
    kubectl(
        "-n",
        ns,
        "wait",
        "--for=condition=ready",
        f"pod/{manifest['metadata']['name']}",
        "--timeout=300s",
    )


def sh(ns: str, name: str, script: str, stdin: str | None = None) -> str:
    return kubectl("-n", ns, "exec", "-i", name, "--", "sh", "-c", script, stdin=stdin)


def counts(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        if "\t" in line:
            table, rows = line.rsplit("\t", 1)
            out[table] = int(rows)
    return out


def drill_postgres(ns: str, report: dict) -> None:
    name = f"{DRILL}-postgres"
    password = secrets.token_hex(16)
    start(
        ns,
        pod(
            ns,
            name,
            PG_IMAGE,
            ["docker-entrypoint.sh", "postgres"],
            {"POSTGRES_PASSWORD": password, "PGDATA": "/drill/pg"},
            "postgres-backups",
        ),
    )
    # The server takes a moment after the container is ready.
    sh(ns, name, "until pg_isready -q -U postgres; do sleep 1; done")
    ts = sh(ns, name, "ls /backups/dumps/roles_*.sql.gz | sort | tail -1").strip()
    ts = ts.rsplit("roles_", 1)[1].removesuffix(".sql.gz")
    dumps = sh(ns, name, f"ls /backups/dumps/ | grep '_{ts}.sql.gz$' | grep '^dump_'").split()
    report["postgres"] = {"backup": ts, "databases": {}}
    t0 = time.monotonic()
    # The roles first; the server's own `postgres` already exists, that error is expected.
    sh(
        ns,
        name,
        f"gunzip -c /backups/dumps/roles_{ts}.sql.gz | psql -q -U postgres >/dev/null 2>&1 || true",
    )
    for dump in dumps:
        db = dump.removeprefix("dump_").removesuffix(f"_{ts}.sql.gz")
        sh(
            ns,
            name,
            f'createdb -U postgres "{db}" && gunzip -c /backups/dumps/{dump} '
            f'| psql -q -v ON_ERROR_STOP=1 -U postgres -d "{db}" >/dev/null',
        )
        restored = counts(sh(ns, name, f'psql -U postgres -d "{db}" -tA', stdin=COUNTS_SQL))
        live = counts(
            kubectl(
                "-n",
                LIVE_DB[0],
                "exec",
                "-i",
                LIVE_DB[1],
                "--",
                "psql",
                "-U",
                "admin",
                "-d",
                db,
                "-tA",
                stdin=COUNTS_SQL,
            )
        )
        missing = sorted(set(live) - set(restored))
        differ = {
            t: (live[t], restored[t]) for t in live if t in restored and live[t] != restored[t]
        }
        report["postgres"]["databases"][db] = {
            "tables": len(restored),
            "rows": sum(restored.values()),
            "missing_tables": missing,
            "rows_differ": differ,
        }
        if missing:
            report["failures"].append(
                f"postgres {db}: tables missing from the restore: {missing[:5]}"
            )
    report["postgres"]["seconds"] = round(time.monotonic() - t0, 1)


def stop(ns: str, name: str) -> None:
    """One drill pod at a time: the one-node dev cluster has its memory requests nearly all taken
    (98 % on 17/11/2026), and the third drill pod stayed Pending on "Insufficient memory"."""
    kubectl("-n", ns, "delete", "pod", name, "--wait=true", check=False)


def drill_stores(ns: str, report: dict) -> None:
    user, password = "drill", secrets.token_hex(16)
    minio = f"{DRILL}-minio"
    start(
        ns,
        pod(
            ns,
            minio,
            MINIO_IMAGE,
            ["/opt/bitnami/scripts/minio/run.sh"],
            {
                "MINIO_ROOT_USER": user,
                "MINIO_ROOT_PASSWORD": password,
                "MINIO_DATA_DIR": "/drill/minio",
                "MC_CONFIG_DIR": "/drill/.mc",
            },
            "store-backups",
            user=1001,
        ),
    )
    qdrant = f"{DRILL}-qdrant"
    start(
        ns,
        pod(
            ns,
            qdrant,
            QDRANT_IMAGE,
            ["./entrypoint.sh"],
            {
                "QDRANT__STORAGE__STORAGE_PATH": "/drill/storage",
                "QDRANT__STORAGE__SNAPSHOTS_PATH": "/drill/snapshots",
            },
            None,
        ),
    )
    qdrant_ip = kubectl("-n", ns, "get", "pod", qdrant, "-o", "jsonpath={.status.podIP}").strip()

    t0 = time.monotonic()
    out = sh(
        ns,
        minio,
        f"""
set -eu
until mc alias set drill http://127.0.0.1:9000 {user} {password} >/dev/null 2>&1; do sleep 1; done
for b in $(ls /backups/minio); do mc mb -q drill/$b >/dev/null; done
mc mirror -q --overwrite /backups/minio drill >/dev/null
for b in $(ls /backups/minio); do echo "$b $(mc ls -r drill/$b | wc -l)"; done
""",
    )
    restored = {b: int(n) for b, n in (line.split() for line in out.splitlines() if line.strip())}
    live_out = kubectl(
        "-n",
        "dataagent-database",
        "exec",
        "minio-0",
        "--",
        "bash",
        "-c",
        """
export MC_CONFIG_DIR=/tmp/.mc-drill
mc alias set live http://127.0.0.1:8360 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
for b in $(mc ls live | awk '{print $NF}' | tr -d /); do echo "$b $(mc ls -r live/$b | wc -l)"; done
rm -rf /tmp/.mc-drill""",
    )
    live = {b: int(n) for b, n in (line.split() for line in live_out.splitlines() if line.strip())}
    report["minio"] = {
        "restored": restored,
        "live": live,
        "seconds": round(time.monotonic() - t0, 1),
    }
    for b, n in live.items():
        if restored.get(b, 0) < n:
            report["failures"].append(f"minio {b}: {restored.get(b, 0)} objects restored, {n} live")

    t0 = time.monotonic()
    latest = sh(ns, minio, "ls /backups/qdrant | sort | tail -1").strip()
    report["qdrant"] = {"backup": latest, "collections": {}}
    for snap in sh(ns, minio, f"ls /backups/qdrant/{latest}").split():
        c = snap.removesuffix(".snapshot")
        sh(
            ns,
            minio,
            f'curl -fsS -X POST "http://{qdrant_ip}:6333/collections/{c}/snapshots/upload?priority=snapshot&wait=true" '
            f"-F snapshot=@/backups/qdrant/{latest}/{snap} >/dev/null",
        )
        got = json.loads(sh(ns, minio, f"curl -fsS http://{qdrant_ip}:6333/collections/{c}"))[
            "result"
        ]["points_count"]
        live_points = json.loads(
            kubectl(
                "-n",
                "dataagent-service",
                "exec",
                "deploy/api",
                "--",
                "python",
                "-c",
                f"import urllib.request; print(urllib.request.urlopen('{LIVE_QDRANT}/collections/{c}').read().decode())",
            )
        )["result"]["points_count"]
        report["qdrant"]["collections"][c] = {"restored": got, "live": live_points}
        if got != live_points:
            report["failures"].append(f"qdrant {c}: {got} points restored, {live_points} live")
    report["qdrant"]["seconds"] = round(time.monotonic() - t0, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--namespace", default="dataagent-service")
    parser.add_argument("--keep", action="store_true", help="leave the drill pods for a look")
    args = parser.parse_args(argv)
    report: dict = {"failures": []}
    started = time.monotonic()
    try:
        drill_postgres(args.namespace, report)
        if not args.keep:
            stop(args.namespace, f"{DRILL}-postgres")
        drill_stores(args.namespace, report)
    except SystemExit as e:
        # A step that broke is a finding of the drill; what ran before it is still reported.
        report["failures"].append(str(e))
    finally:
        if not args.keep:
            kubectl(
                "-n",
                args.namespace,
                "delete",
                "pod",
                "-l",
                f"app={DRILL}",
                "--wait=false",
                check=False,
            )
    report["seconds"] = round(time.monotonic() - started, 1)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
