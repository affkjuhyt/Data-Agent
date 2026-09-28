"""Unit tests for shared.atomic_io — crash-safe state-file writes.

Used by the platform-workers audit bridge to persist poll cursors / last-seen
key ids. The whole point is that a write never leaves a truncated file, so the
tests assert the result is all-or-nothing and that no temp files leak.
"""
from __future__ import annotations

import os

import pytest

from libs.shared.atomic_io import atomic_write_text

pytestmark = pytest.mark.unit


def test_writes_content(tmp_path) -> None:
    target = tmp_path / "state.txt"
    atomic_write_text(str(target), "kid-123")
    assert target.read_text(encoding="utf-8") == "kid-123"


def test_overwrites_existing_file(tmp_path) -> None:
    target = tmp_path / "state.txt"
    target.write_text("old-value", encoding="utf-8")
    atomic_write_text(str(target), "new-value")
    assert target.read_text(encoding="utf-8") == "new-value"


def test_creates_missing_parent_dir(tmp_path) -> None:
    target = tmp_path / "nested" / "deep" / "state.txt"
    atomic_write_text(str(target), "x")
    assert target.read_text(encoding="utf-8") == "x"


def test_roundtrips_unicode(tmp_path) -> None:
    target = tmp_path / "state.txt"
    # Vietnamese diacritics — the domain this codebase cares about.
    atomic_write_text(str(target), "Đà Nẵng")
    assert target.read_text(encoding="utf-8") == "Đà Nẵng"


def test_leaves_no_temp_file_behind(tmp_path) -> None:
    target = tmp_path / "state.txt"
    atomic_write_text(str(target), "done")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_cleans_up_temp_file_on_write_failure(tmp_path, monkeypatch) -> None:
    """If the swap fails mid-write, the temp file must not be left behind."""
    target = tmp_path / "state.txt"

    def boom(*_args, **_kwargs):
        raise OSError("simulated disk failure during replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(str(target), "partial")

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == [], f"temp file not cleaned up after failure: {leftovers}"
    assert not target.exists()
