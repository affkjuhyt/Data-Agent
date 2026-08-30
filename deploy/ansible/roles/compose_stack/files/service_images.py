"""Images for the given compose services (one per line, deduplicated).

    docker compose … --profile runtime config --format json | python3 service_images.py svc1,svc2 [--missing]

Only the listed services count: `docker compose config --images` with the
runtime profile also lists the KB tier, which this stack does not deploy.
--missing prints the SERVICE names whose image is not present locally instead.
"""
import json
import subprocess
import sys

cfg = json.load(sys.stdin)
project = cfg.get("name", "infra")
wanted = [s for s in sys.argv[1].split(",") if s] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else list(cfg["services"])
missing_mode = "--missing" in sys.argv[1:]

seen: set[str] = set()
for name in wanted:
    svc = cfg["services"].get(name)
    if svc is None:
        continue
    image = svc.get("image") or f"{project}-{name}"
    if missing_mode:
        if "build" in svc and subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
            print(name)
    elif image not in seen:
        seen.add(image)
        print(image)
