"""platform-workers entrypoint — ``WORKER`` selects which loop this container runs.

    WORKER=memory          -> workers.memory.run()          (memory.extraction.requested)
    WORKER=schema-crawler  -> workers.schema_crawler.run()  (cron crawl + ddl.changes)
    WORKER=audit           -> workers.audit.run()           (audit.data.access + Keycloak bridge)

One image, three containers: each keeps its own Kafka principal, consumer
group and restart policy, so a crash in one worker cannot take the others down.
"""
from __future__ import annotations

import importlib
import os
import sys

WORKERS: dict[str, str] = {
    "memory": "workers.memory",
    "schema-crawler": "workers.schema_crawler",
    "audit": "workers.audit",
}


def main() -> int:
    name = os.environ.get("WORKER", "").strip()
    module_name = WORKERS.get(name)
    if module_name is None:
        print(
            f"platform-workers: WORKER={name!r} is not one of {sorted(WORKERS)}",
            file=sys.stderr,
        )
        return 2
    importlib.import_module(module_name).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
