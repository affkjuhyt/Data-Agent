"""Signed download links for answer exports (BR-86): worker-agent writes the file to MinIO
`da-exports/{org_id}/{job_id}.{csv|xlsx}`; the api only signs a short GET for its requester."""

from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlparse

from services.api.kernel.problems import Problem

EXPORT_BUCKET = "da-exports"
# A3: the link is signed for 15 minutes; the file itself lives 24 hours.
LINK_SECONDS = 900


class ExportLinks(Protocol):
    async def sign(self, key: str) -> str | None: ...


class UnconfiguredExportLinks:
    async def sign(self, key: str) -> str | None:
        return None


class MinioExportLinks:
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
                    Params={"Bucket": EXPORT_BUCKET, "Key": key},
                    ExpiresIn=LINK_SECONDS,
                )
                return link
        except Exception as exc:
            raise Problem(503, "source_unavailable", "Export storage is unavailable.") from exc


def export_links() -> ExportLinks:
    url = os.getenv("MINIO_URL", "").strip()
    return MinioExportLinks(url) if url else UnconfiguredExportLinks()
