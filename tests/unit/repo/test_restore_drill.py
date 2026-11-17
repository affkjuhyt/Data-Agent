"""tests/ops/restore_drill.py (PL-04): the parts that do not need a cluster."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

pytestmark = [pytest.mark.unit]

_path = pathlib.Path(__file__).parents[2] / "ops" / "restore_drill.py"
_spec = importlib.util.spec_from_file_location("restore_drill", _path)
assert _spec is not None and _spec.loader is not None
drill = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = drill
_spec.loader.exec_module(drill)


def test_row_counts_are_read_per_table() -> None:
    assert drill.counts("public.a\t3\nkeycloak.realm\t1\n\n") == {
        "public.a": 3,
        "keycloak.realm": 1,
    }


def test_a_drill_pod_reads_the_backups_and_writes_nowhere_else() -> None:
    pod = drill.pod("ns", "restore-drill-x", "img", ["run"], {"K": "v"}, "postgres-backups")
    mounts = {m["mountPath"]: m for m in pod["spec"]["containers"][0]["volumeMounts"]}
    assert mounts["/backups"]["readOnly"] is True
    claim = next(v for v in pod["spec"]["volumes"] if v["name"] == "backups")
    assert claim["persistentVolumeClaim"] == {"claimName": "postgres-backups", "readOnly": True}
    # Every drill pod carries the label the clean-up deletes by.
    assert pod["metadata"]["labels"]["app"] == drill.DRILL
    assert pod["spec"]["automountServiceAccountToken"] is False
