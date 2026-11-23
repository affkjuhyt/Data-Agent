"""The framed body worker-agent and the python sandbox exchange when datasets travel as Arrow (AL-04).

    MAGIC (4 bytes) · header length (4 bytes, big-endian) · header (UTF-8 JSON) · parts

The header says what each part is and how long it is (`parts`: byte lengths, in order); the parts
are Arrow IPC streams (libs/snapshot/ipc.py), read only after the lengths add up to the body.
A request's header holds the code and the datasets' names; a reply's holds ok, error, stdout and
truncated, with the result table as its one part (none when the code left no table).

JSON stays a transport for one release (AGENT_SANDBOX_TRANSPORT=json): the pod answers either,
told apart by the Content-Type.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
from typing import Any

CONTENT_TYPE = "application/x-dataagent-sandbox"
MAGIC = b"DAS1"
MAX_HEADER = 256 * 1024


class FrameInvalid(ValueError):
    """The body is not one well-formed frame."""


def pack(header: dict[str, Any], parts: Sequence[bytes]) -> bytes:
    head = json.dumps({**header, "parts": [len(p) for p in parts]}, ensure_ascii=False).encode()
    if len(head) > MAX_HEADER:
        raise FrameInvalid("header too large")
    return MAGIC + struct.pack(">I", len(head)) + head + b"".join(parts)


def unpack(body: bytes) -> tuple[dict[str, Any], list[bytes]]:
    if len(body) < 8 or body[:4] != MAGIC:
        raise FrameInvalid("not a sandbox frame")
    (size,) = struct.unpack(">I", body[4:8])
    if size > MAX_HEADER or 8 + size > len(body):
        raise FrameInvalid("header length out of bounds")
    try:
        header = json.loads(body[8 : 8 + size])
    except ValueError as exc:
        raise FrameInvalid("header is not JSON") from exc
    lengths = header.get("parts") if isinstance(header, dict) else None
    if not isinstance(lengths, list) or not all(isinstance(n, int) and n >= 0 for n in lengths):
        raise FrameInvalid("parts are not lengths")
    if 8 + size + sum(lengths) != len(body):
        raise FrameInvalid("parts do not add up to the body")
    parts, at = [], 8 + size
    for n in lengths:
        parts.append(body[at : at + n])
        at += n
    return header, parts
