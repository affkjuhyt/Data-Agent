"""schema_hash (sandbox_schema_docs.docx §2.7) — content fingerprint used for
incremental re-crawl. Sorting before hashing means column *order* (e.g. a
new column inserted mid-table) doesn't itself flip the hash."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_same_columns_same_hash():
    from workers.schema_crawler.schema_hash import schema_hash
    cols = [{"name": "a", "type": "VARCHAR"}, {"name": "b", "type": "NUMBER"}]
    assert schema_hash(cols) == schema_hash(cols)


@pytest.mark.unit
def test_column_order_does_not_affect_hash():
    from workers.schema_crawler.schema_hash import schema_hash
    a = [{"name": "a", "type": "VARCHAR"}, {"name": "b", "type": "NUMBER"}]
    b = [{"name": "b", "type": "NUMBER"}, {"name": "a", "type": "VARCHAR"}]
    assert schema_hash(a) == schema_hash(b)


@pytest.mark.unit
def test_added_column_changes_hash():
    from workers.schema_crawler.schema_hash import schema_hash
    before = [{"name": "a", "type": "VARCHAR"}]
    after = [{"name": "a", "type": "VARCHAR"}, {"name": "b", "type": "NUMBER"}]
    assert schema_hash(before) != schema_hash(after)


@pytest.mark.unit
def test_type_change_changes_hash():
    from workers.schema_crawler.schema_hash import schema_hash
    before = [{"name": "a", "type": "VARCHAR"}]
    after = [{"name": "a", "type": "NUMBER"}]
    assert schema_hash(before) != schema_hash(after)


@pytest.mark.unit
def test_extra_keys_like_comment_do_not_affect_hash():
    from workers.schema_crawler.schema_hash import schema_hash
    a = [{"name": "a", "type": "VARCHAR", "comment": "x"}]
    b = [{"name": "a", "type": "VARCHAR", "comment": "different comment"}]
    assert schema_hash(a) == schema_hash(b)
