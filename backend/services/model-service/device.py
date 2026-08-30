"""Compute-device auto-detection. The GPU code path always exists; the service
just probes what the host actually has (NVIDIA CUDA > Apple MPS > CPU) and uses
that — so the same image runs on a GPU box in prod and on this GPU-less Mac. The
result is surfaced at GET /capabilities ("what does this server have to use")."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def detect_device() -> str:
    """Best available device, detected once. Override with EMBED_DEVICE env."""
    forced = os.getenv("EMBED_DEVICE", "").strip().lower()
    if forced:
        log.info("compute device forced via EMBED_DEVICE=%s", forced)
        return forced
    try:
        import torch

        if torch.cuda.is_available():
            log.info("compute device: cuda (%s)", torch.cuda.get_device_name(0))
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            log.info("compute device: mps (Apple Metal)")
            return "mps"
    except Exception as e:  # noqa: BLE001 — probe must never crash startup
        log.warning("device probe failed, falling back to cpu: %s", e)
    log.info("compute device: cpu (no GPU detected)")
    return "cpu"


@lru_cache(maxsize=1)
def capabilities() -> dict[str, Any]:
    """Hardware/compute the server can use — the payload of /capabilities."""
    caps: dict[str, Any] = {
        "device": detect_device(),
        "cpu_count": os.cpu_count(),
    }
    try:
        import torch

        caps["torch"] = torch.__version__
        cuda = bool(torch.cuda.is_available())
        caps["cuda_available"] = cuda
        caps["cuda_devices"] = torch.cuda.device_count() if cuda else 0
        if cuda:
            caps["cuda_name"] = torch.cuda.get_device_name(0)
        mps = getattr(torch.backends, "mps", None)
        caps["mps_available"] = bool(mps is not None and mps.is_available())
    except Exception as e:  # noqa: BLE001
        caps["torch"] = None
        caps["probe_error"] = str(e)
    return caps
