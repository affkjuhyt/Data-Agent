"""Atomic file helpers shared across services.

Plain ``open(path, "w")`` truncates the target up front, so a crash between the
truncate and the write completing leaves an empty or half-written file. State
files (poll cursors, last-seen key ids) read back corrupt and the service falls
back to a costly cold-start path.

``atomic_write_text`` writes to a temp file in the same directory, fsyncs it,
then ``os.replace`` — an atomic rename on POSIX and Windows (same filesystem).
A kill at any point leaves either the old complete file or the new one, never a
truncated one.
"""
from __future__ import annotations

import os
import tempfile


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    # Temp file in the SAME directory → same filesystem → os.replace is atomic.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # ensure bytes hit disk before the rename
        os.replace(tmp, path)  # atomic swap
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
