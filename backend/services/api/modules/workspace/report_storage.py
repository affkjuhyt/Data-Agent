"""Read-only access to report documents stored by the report worker."""

from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlparse

from services.api.kernel.problems import Problem

REPORT_BUCKET = "da-results"


class ReportStore(Protocol):
    async def download(self, bucket: str, key: str) -> bytes: ...


class UnconfiguredReportStore:
    async def download(self, bucket: str, key: str) -> bytes:
        raise Problem(503, "source_unavailable", "Report storage is not configured.")


class MinioReportStore:
    """Small read-only S3 adapter; workers remain responsible for writing artifacts."""

    async def download(self, bucket: str, key: str) -> bytes:
        url = os.getenv("MINIO_URL", "").strip()
        if not url:
            raise Problem(503, "source_unavailable", "Report storage is not configured.")

        # Keep the API image independent from the worker's connector package at import time.
        import aioboto3
        from botocore.config import Config
        from botocore.exceptions import ClientError

        parsed = urlparse(url)
        endpoint = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            endpoint += f":{parsed.port}"
        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=parsed.username,
                aws_secret_access_key=parsed.password,
                config=Config(signature_version="s3v4"),
            ) as client:
                try:
                    response = await client.get_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    code = str(exc.response.get("Error", {}).get("Code", ""))
                    if code in {"404", "NoSuchKey", "NoSuchBucket"}:
                        raise FileNotFoundError(key) from exc
                    raise
                # botocore types the body as Any; the object is a document of bytes.
                body: bytes = await response["Body"].read()
                return body
        except FileNotFoundError:
            raise
        except Problem:
            raise
        except Exception as exc:
            raise Problem(503, "source_unavailable", "Report storage is unavailable.") from exc


def report_store() -> ReportStore:
    return MinioReportStore() if os.getenv("MINIO_URL", "").strip() else UnconfiguredReportStore()
