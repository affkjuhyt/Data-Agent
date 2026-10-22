"""Originals in MinIO da-raw-crawl (BR-150): content-addressed, never overwritten, kept 400 days.

The bucket is created with object lock and a default retention by the minio-init Job, so a stored
original cannot be deleted before its date even by the crawler's own user; the row's retain_until
records the same date for the re-extraction path (UC-14-03 E1).
"""

from __future__ import annotations

import datetime
import os
from typing import Protocol
from urllib.parse import unquote, urlsplit

import aioboto3
from botocore.exceptions import ClientError

BUCKET = os.environ.get("CRAWLER_RAW_BUCKET", "da-raw-crawl")
RETENTION_DAYS = int(os.environ.get("CRAWLER_RETENTION_DAYS", "400"))


def retain_until(now: datetime.datetime) -> datetime.datetime:
    return now + datetime.timedelta(days=RETENTION_DAYS)


class RawStore(Protocol):
    async def put(self, key: str, content: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class S3RawStore:
    """MINIO_URL is http://<user>:<password>@<host>:<port>, as every V3 service reads it."""

    def __init__(self, url: str | None = None) -> None:
        parts = urlsplit(url or os.environ["MINIO_URL"])
        self._endpoint = f"{parts.scheme}://{parts.hostname}:{parts.port}"
        self._key, self._secret = unquote(parts.username or ""), unquote(parts.password or "")
        self._session = aioboto3.Session()

    def _client(self):  # type: ignore[no-untyped-def]
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._key,
            aws_secret_access_key=self._secret,
            region_name="us-east-1",
        )

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=BUCKET, Key=key)
                return  # same bytes already stored: the key is their SHA-256
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in (
                    "404",
                    "NoSuchKey",
                    "NotFound",
                ):
                    raise
            await s3.put_object(Bucket=BUCKET, Key=key, Body=content, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            response = await s3.get_object(Bucket=BUCKET, Key=key)
            async with response["Body"] as body:
                return bytes(await body.read())
