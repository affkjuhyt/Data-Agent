"""platform-workers entrypoint — ``WORKER`` selects which loop this container runs.

    WORKER=memory          -> workers.memory.run()          (memory.extraction.requested)
    WORKER=schema-crawler  -> workers.schema_crawler.run()  (cron crawl + ddl.changes)
    WORKER=audit           -> workers.audit.run()           (audit.data.access + Keycloak bridge)

One image, three containers: each keeps its own Kafka principal, consumer
group and restart policy, so a crash in one worker cannot take the others down.
"""
from __future__ import annotations

import importlib
import sys

from services.platform_workers.app import (
    WORKERS,
    resolve_worker_module,
    unknown_worker_message,
    worker_name_from_env,
)


def main() -> int:
    name = worker_name_from_env()
    module_name = resolve_worker_module(name)
    if module_name is None:
        print(unknown_worker_message(name), file=sys.stderr)
        return 2
    importlib.import_module(module_name).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
