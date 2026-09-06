import json
import pathlib
import sys

import yaml

WORKLOADS = {"Deployment", "StatefulSet"}


def cpu(q):
    """Kubernetes CPU quantity -> millicores."""
    if not q:
        return 0
    q = str(q)
    return int(float(q[:-1])) if q.endswith("m") else int(float(q) * 1000)


def mem(q):
    """Kubernetes memory quantity -> bytes."""
    if not q:
        return 0
    q = str(q)
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
             "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
    for suffix, mult in units.items():
        if q.endswith(suffix):
            return int(float(q[: -len(suffix)]) * mult)
    return int(float(q))


def gib(b):
    return b / 2**30


def main(argv):
    nodes = json.loads(pathlib.Path(argv[1]).read_text())
    alloc_cpu = sum(cpu(n["status"]["allocatable"]["cpu"]) for n in nodes["items"])
    alloc_mem = sum(mem(n["status"]["allocatable"]["memory"]) for n in nodes["items"])

    want_cpu = want_mem = 0
    rows = []
    for d in argv[2:]:
        for path in sorted(pathlib.Path(d).glob("*.yaml")):
            for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if not doc or doc.get("kind") not in WORKLOADS:
                    continue
                replicas = doc["spec"].get("replicas", 1)
                pod = doc["spec"]["template"]["spec"]
                c = m = 0
                for ctr in pod.get("initContainers", []) + pod.get("containers", []):
                    req = (ctr.get("resources") or {}).get("requests") or {}
                    c += cpu(req.get("cpu"))
                    m += mem(req.get("memory"))
                c *= replicas
                m *= replicas
                want_cpu += c
                want_mem += m
                rows.append((doc["metadata"]["name"], c, m))

    rows.sort(key=lambda r: -r[2])
    print(f"{'workload':<28}{'cpu':>10}{'memory':>12}")
    for name, c, m in rows:
        print(f"{name:<28}{str(c) + 'm':>10}{gib(m):>10.1f}Gi")
    print(f"{'-' * 50}")
    print(f"{'requested':<28}{str(want_cpu) + 'm':>10}{gib(want_mem):>10.1f}Gi")
    print(f"{'allocatable':<28}{str(alloc_cpu) + 'm':>10}{gib(alloc_mem):>10.1f}Gi")

    over = []
    if want_cpu > alloc_cpu:
        over.append(f"cpu short by {want_cpu - alloc_cpu}m")
    if want_mem > alloc_mem:
        over.append(f"memory short by {gib(want_mem - alloc_mem):.1f}Gi")
    if over:
        print("\nWILL NOT SCHEDULE: " + "; ".join(over))
        print("Trim database_workloads / apply_services, or give the VM more room.")
        return 1
    print("\nfits")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
