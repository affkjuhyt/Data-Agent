"""The bucket snapshots live in: da-snapshots, object-locked (deploy/k8s/*/job/minio-init.yaml).

MINIO_URL carries the endpoint and the credentials (http://key:secret@host:port), as every other
writer of MinIO takes it. A key is content-addressed (`<org>/<dataset>/v<n>/<sha256[:12]>.parquet`),
so writing it twice writes the same bytes: the put asks MinIO not to overwrite (If-None-Match) and
takes "already there" as done.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

BUCKET = os.environ.get("SNAPSHOT_BUCKET", "da-snapshots")
CONTENT_TYPE = "application/vnd.apache.parquet"


class StoreUnavailable(RuntimeError):
    """No object store is configured, or it refused the snapshot."""


class SnapshotStore(Protocol):
    async def put(self, key: str, path: Path) -> bool:
        """Store the file at `key`; False when the same key was there already."""
        ...

    async def get(self, key: str, path: Path) -> None:
        """Download `key` into `path`."""
        ...


class MinioStore:
    def __init__(self, url: str, bucket: str = BUCKET) -> None:
        parsed = urlparse(url)
        if not parsed.hostname:
            raise StoreUnavailable("MINIO_URL is not set")
        self._endpoint = f"{parsed.scheme}://{parsed.hostname}" + (
            f":{parsed.port}" if parsed.port else ""
        )
        self._key, self._secret = parsed.username, parsed.password
        self.bucket = bucket

    @classmethod
    def from_env(cls) -> MinioStore:
        return cls(os.environ.get("MINIO_URL", ""))

    def _client(self):  # type: ignore[no-untyped-def]
        import aioboto3
        from botocore.config import Config

        return aioboto3.Session().client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._key,
            aws_secret_access_key=self._secret,
            config=Config(signature_version="s3v4"),
        )

    async def put(self, key: str, path: Path) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with self._client() as client:
                with path.open("rb") as body:
                    await client.put_object(
                        Bucket=self.bucket,
                        Key=key,
                        Body=body,
                        ContentType=CONTENT_TYPE,
                        IfNoneMatch="*",
                    )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}:
                return False
            raise StoreUnavailable(f"the object store refused the snapshot: {exc}") from exc
        except BotoCoreError as exc:
            raise StoreUnavailable(f"the object store is unreachable: {exc}") from exc

    async def get(self, key: str, path: Path) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            async with self._client() as client:
                response = await client.get_object(Bucket=self.bucket, Key=key)
                with path.open("wb") as out:
                    async for chunk in response["Body"].iter_chunks():
                        out.write(chunk)
        except (BotoCoreError, ClientError) as exc:
            raise StoreUnavailable(f"the snapshot {key} cannot be read: {exc}") from exc
