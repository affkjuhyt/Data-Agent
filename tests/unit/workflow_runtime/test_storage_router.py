"""workflow_runtime /api/storage/* (folded in from the former minio_service).

The only consumer is the Settings > Storage panel; what is pinned here is the
JSON contract it reads (frontend/src/lib/usePlatformData.ts):

    GET /api/storage/buckets                    -> {"buckets": [str]}
    GET /api/storage/buckets/{b}/objects?prefix -> {"bucket": str, "objects": [{key, size, last_modified}]}

boto3 is replaced by a fake client; nothing here reaches the network.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit

pytest.importorskip("fastapi", reason="fastapi not installed in this environment")
pytest.importorskip("boto3", reason="boto3 not installed in this environment")

from botocore.exceptions import ClientError, EndpointConnectionError  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(autouse=True)
def _sys_path():
    # The service dir must be first: other services also ship a `routers`
    # package and a stale sys.modules entry would resolve to theirs.
    was_present = _SVC_DIR in sys.path
    if was_present:
        sys.path.remove(_SVC_DIR)
    sys.path.insert(0, _SVC_DIR)
    for name in list(sys.modules):
        if name.split(".")[0] == "routers":
            file = getattr(sys.modules[name], "__file__", None) or ""
            if file and not file.startswith(_SVC_DIR):
                del sys.modules[name]
    yield
    if not was_present and _SVC_DIR in sys.path:
        sys.path.remove(_SVC_DIR)


def _storage():
    import routers.storage as m
    return m


class _FakeS3:
    def __init__(self, buckets: list[str], objects: dict[str, list[dict[str, Any]]]) -> None:
        self._buckets = buckets
        self._objects = objects
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_buckets(self) -> dict[str, Any]:
        self.calls.append(("list_buckets", {}))
        return {"Buckets": [{"Name": n, "CreationDate": dt.datetime(2026, 1, 1)} for n in self._buckets]}

    def list_objects_v2(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("list_objects_v2", kw))
        bucket = kw["Bucket"]
        if bucket not in self._objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchBucket", "Message": "The specified bucket does not exist"}},
                "ListObjectsV2",
            )
        prefix = kw.get("Prefix", "")
        contents = [o for o in self._objects[bucket] if o["Key"].startswith(prefix)]
        return {"Contents": contents} if contents else {}


def _app(fake: Any) -> TestClient:
    storage = _storage()
    storage.s3_client = lambda: fake  # type: ignore[assignment]
    app = FastAPI()
    app.include_router(storage.router)
    return TestClient(app)


_TS = dt.datetime(2026, 8, 30, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def fake() -> _FakeS3:
    return _FakeS3(
        buckets=["aeroflow-artifacts", "sandbox-code"],
        objects={
            "aeroflow-artifacts": [
                {"Key": "t1/report.csv", "Size": 1234, "LastModified": _TS},
                {"Key": "t2/chart.png", "Size": 99, "LastModified": _TS},
            ],
            "sandbox-code": [],
        },
    )


class TestListBuckets:
    def test_returns_names_only_under_buckets_key(self, fake):
        client = _app(fake)
        r = client.get("/api/storage/buckets")
        assert r.status_code == 200
        assert r.json() == {"buckets": ["aeroflow-artifacts", "sandbox-code"]}

    def test_empty_account_is_an_empty_list(self):
        client = _app(_FakeS3(buckets=[], objects={}))
        assert client.get("/api/storage/buckets").json() == {"buckets": []}

    def test_unreachable_storage_is_503_not_500(self):
        class _Down:
            def list_buckets(self):
                raise EndpointConnectionError(endpoint_url="http://minio:9000")

        client = _app(_Down())
        r = client.get("/api/storage/buckets")
        assert r.status_code == 503
        assert "unavailable" in r.json()["detail"]


class TestListObjects:
    def test_shape_matches_frontend_bucket_object_type(self, fake):
        client = _app(fake)
        r = client.get("/api/storage/buckets/aeroflow-artifacts/objects")
        assert r.status_code == 200
        body = r.json()
        assert body["bucket"] == "aeroflow-artifacts"
        assert body["objects"] == [
            {"key": "t1/report.csv", "size": 1234, "last_modified": "2026-08-30T12:00:00+00:00"},
            {"key": "t2/chart.png", "size": 99, "last_modified": "2026-08-30T12:00:00+00:00"},
        ]
        # Exactly the three fields the BucketObject interface declares.
        assert set(body["objects"][0]) == {"key", "size", "last_modified"}

    def test_prefix_query_param_is_forwarded(self, fake):
        client = _app(fake)
        r = client.get("/api/storage/buckets/aeroflow-artifacts/objects", params={"prefix": "t2/"})
        assert r.status_code == 200
        assert [o["key"] for o in r.json()["objects"]] == ["t2/chart.png"]
        assert fake.calls[-1] == ("list_objects_v2", {"Bucket": "aeroflow-artifacts", "Prefix": "t2/"})

    def test_missing_prefix_sends_empty_string(self, fake):
        client = _app(fake)
        client.get("/api/storage/buckets/sandbox-code/objects")
        assert fake.calls[-1][1]["Prefix"] == ""

    def test_bucket_with_no_contents_is_an_empty_list(self, fake):
        client = _app(fake)
        r = client.get("/api/storage/buckets/sandbox-code/objects")
        assert r.json() == {"bucket": "sandbox-code", "objects": []}

    def test_unknown_bucket_is_404(self, fake):
        client = _app(fake)
        r = client.get("/api/storage/buckets/nope/objects")
        assert r.status_code == 404
        assert "NoSuchBucket" in r.json()["detail"]

    def test_bucket_name_is_url_decoded(self, fake):
        client = _app(fake)
        r = client.get("/api/storage/buckets/aeroflow%2Dartifacts/objects")
        assert r.status_code == 200
        assert r.json()["bucket"] == "aeroflow-artifacts"


class TestClientConfig:
    def test_reads_the_minio_env_names_the_compose_block_sets(self, monkeypatch):
        storage = _storage()
        seen: dict[str, Any] = {}

        def _fake_boto_client(service: str, **kw: Any) -> str:
            seen["service"] = service
            seen.update(kw)
            return "client"

        monkeypatch.setattr(storage.boto3, "client", _fake_boto_client)
        monkeypatch.setenv("MINIO_ENDPOINT", "http://minio.test:9000")
        monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
        monkeypatch.setenv("MINIO_SECRET_KEY", "sk")

        assert storage.s3_client() == "client"
        assert seen == {
            "service": "s3",
            "endpoint_url": "http://minio.test:9000",
            "aws_access_key_id": "ak",
            "aws_secret_access_key": "sk",
        }


class TestRouterWiring:
    def test_app_includes_storage_in_the_authenticated_loop(self):
        """Every router except health is included through the `authed` loop in
        app.py; a storage router outside it would be reachable with no caller
        identity, which is the defect the old service had."""
        import ast

        tree = ast.parse((pathlib.Path(_SVC_DIR) / "app.py").read_text(encoding="utf-8"))
        loop = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple)
        )
        members = {
            f"{e.value.id}.{e.attr}" for e in loop.iter.elts
            if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name)
        }
        assert "storage.router" in members
