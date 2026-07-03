"""Content-hash for diff detection (doc §2.7) — only re-embed/re-describe a
table whose columns actually changed since the last crawl.

Sorting before hashing means adding a column in the middle of a table
doesn't itself flip the hash via column order — only an actual change to
the (name, type) set does.
"""
import hashlib


def schema_hash(columns: list[dict]) -> str:
    key = sorted(f"{c['name']}:{c['type']}" for c in columns)
    return hashlib.md5("|".join(key).encode()).hexdigest()  # noqa: S324 — content fingerprint, not security-sensitive
