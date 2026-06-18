"""Named-logger factory for the database connectors.

Previously this attached its own file + stream handlers (writing to the repo
`logs/` dir) and set ``propagate = False`` — producing a plain log format that
diverged from the structured JSON the services emit via
``services/shared/observability.py``. It now just returns a standard named logger
that PROPAGATES to the root logger, so each process formats connector logs with
its own root config consistently:

* services (kb_backend, flow-builder, workflow-runtime, …) → JSON via
  ``setup_observability()``;
* the ingestion worker → its ``logging.basicConfig()``.

No own handlers, no file writes. ``file_name`` is kept for call-site
compatibility but is now ignored.
"""
import logging


def create_logger(logger_name: str, file_name: str | None = None) -> logging.Logger:
    log = logging.getLogger(logger_name)
    log.setLevel(logging.INFO)
    return log
