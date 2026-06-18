"""Tiny stdlib HTTP helper — POST/GET JSON to a remote GPU server (e.g. Colab +
cloudflared). Uses urllib so the model-service image needs no extra dependency
(fast rebuild). The remote *backends* (backends/remote.py) build on this."""
from __future__ import annotations

import json
import urllib.request

import config as cfg


def post(base_url: str, path: str, payload: dict, api_key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    key = api_key if api_key is not None else cfg.REMOTE_API_KEY
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.REMOTE_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def get(base_url: str, path: str, api_key: str | None = None) -> dict:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))
