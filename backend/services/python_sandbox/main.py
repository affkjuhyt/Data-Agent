"""python-sandbox: runs the analysis code worker-agent sends (libs/code_sandbox) in a pod built
to have nothing worth reaching: no network out (its NetworkPolicy admits worker-agent in and lets
nothing out), no secrets, no service account token, a read-only root filesystem with /tmp alone
writable, a non-root user and the runtime's seccomp profile. The static check and the audit hook
of libs/code_sandbox/local.py still run inside; this pod is the boundary they are not.

POST /run {"code": "...", "datasets": {"d1": {"columns": [...], "rows": [[...]]}}}
  → {"ok", "columns", "rows", "truncated", "stdout", "error"}
GET /healthz → 200
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
from typing import Any

from libs.code_sandbox import local

logger = logging.getLogger("python_sandbox")

PORT = int(os.environ.get("PYTHON_SANDBOX_PORT", "8140"))
# One run at a time: runs of different askers never share the pod at the same moment (a child
# could otherwise read a sibling's /proc as the same user). Scale with replicas, not with this.
SLOTS = BoundedSemaphore(int(os.environ.get("PYTHON_SANDBOX_CONCURRENCY", "1")))
MAX_BODY = 20 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "python-sandbox"

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(HTTPStatus.OK, {"ok": True})
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            code, datasets = str(payload["code"]), dict(payload.get("datasets") or {})
        except (ValueError, KeyError, TypeError):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
            return
        with SLOTS:
            try:
                done = asyncio.run(local.run(code, datasets))
            except local.CodeRejected as exc:
                self._send(HTTPStatus.OK, {"ok": False, "error": f"code bị từ chối: {exc}"})
                return
        self._send(HTTPStatus.OK, asdict(done))

    def log_message(self, format: str, *args: Any) -> None:
        # Requests are logged without their body: the code and the datasets stay out of logs.
        logger.info("%s %s", self.command, self.path)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info("python-sandbox on :%s", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
