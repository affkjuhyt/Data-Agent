"""Signed download links for a dataset version's Parquet snapshot (DF-05): worker-agent writes it
to MinIO `da-snapshots/<org>/<dataset>/v<n>/<sha256[:12]>.parquet` (libs/snapshot); the api only
signs a short GET for a reader of the dataset, as it signs answer exports (qa/export_storage.py)."""

from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlparse

from services.api.kernel.problems import Problem

SNAPSHOT_BUCKET = os.getenv("SNAPSHOT_BUCKET", "da-snapshots")
# DATA_PIPELINE_SPEC §1 B: a snapshot link is signed for 10 minutes.
LINK_SECONDS = 600


class SnapshotLinks(Protocol):
    async def sign(self, key: str) -> str | None: ...


class UnconfiguredSnapshotLinks:
    async def sign(self, key: str) -> str | None:
        return None


class MinioSnapshotLinks:
    def __init__(self, url: str) -> None:
        self._url = url

    async def sign(self, key: str) -> str | None:
        # Keep the api image importable without the S3 client until a link is asked for.
        import aioboto3
        from botocore.config import Config

        parsed = urlparse(self._url)
        endpoint = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            endpoint += f":{parsed.port}"
        try:
            async with aioboto3.Session().client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=parsed.username,
                aws_secret_access_key=parsed.password,
                config=Config(signature_version="s3v4"),
            ) as client:
                link: str = await client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": SNAPSHOT_BUCKET, "Key": key},
                    ExpiresIn=LINK_SECONDS,
                )
                return link
        except Exception as exc:
            raise Problem(503, "source_unavailable", "Snapshot storage is unavailable.") from exc


def snapshot_links() -> SnapshotLinks:
    url = os.getenv("MINIO_URL", "").strip()
    return MinioSnapshotLinks(url) if url else UnconfiguredSnapshotLinks()
