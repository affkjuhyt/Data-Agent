"""The raw and derived objects of a document in MinIO.

URL format: http://access_key:secret_key@host:port, or http://host:port with credentials from
boto3's default chain (AWS_* env). Moved here from the retired libs/database_connector, whose
only remaining user this was; the health-check loop and the calls nothing made went with it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any, cast
from urllib.parse import urlparse

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)
OPEN_ATTEMPTS = 5


class MinIOClient:
    __slots__ = ("_session", "_endpoint", "_access_key", "_secret_key", "_max_connections")

    def __init__(self) -> None:
        self._session: aioboto3.Session | None = None
        self._endpoint: str | None = None
        self._access_key: str | None = None
        self._secret_key: str | None = None
        self._max_connections = int(os.getenv("MINIO_MAX_CONNECTIONS", "").strip() or 10)

    def set_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.username and parsed.password:
            self._access_key = parsed.username
            self._secret_key = parsed.password
            port = f":{parsed.port}" if parsed.port else ""
            self._endpoint = f"{parsed.scheme}://{parsed.hostname}{port}"
        else:
            self._endpoint = url

    def _client_kwargs(self) -> dict[str, Any]:
        return {
            "endpoint_url": self._endpoint,
            "aws_access_key_id": self._access_key,
            "aws_secret_access_key": self._secret_key,
            "config": Config(max_pool_connections=self._max_connections),
        }

    async def open(self) -> MinIOClient:
        if self._endpoint is None:
            raise RuntimeError("MinIO URL is not set — call set_url() first")
        for attempt in range(OPEN_ATTEMPTS):
            try:
                self._session = aioboto3.Session()
                async with self._session.client("s3", **self._client_kwargs()) as s3:
                    await s3.list_buckets()
                log.info("MinIO connection established endpoint=%s", self._endpoint)
                return self
            except (BotoCoreError, ClientError, OSError) as e:
                log.info(
                    "Attempt #%d/%d: MinIO connection failed — %s", attempt + 1, OPEN_ATTEMPTS, e
                )
                await asyncio.sleep(3)
        raise ConnectionError(f"Could not connect to MinIO after {OPEN_ATTEMPTS} attempts")

    async def close(self) -> None:
        self._session = None

    async def upload(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        # Only called after open() established the session.
        assert self._session is not None
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.upload_fileobj(
                io.BytesIO(data), bucket, key, ExtraArgs={"ContentType": content_type}
            )

    async def download(self, bucket: str, key: str) -> bytes:
        assert self._session is not None
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            resp = await s3.get_object(Bucket=bucket, Key=key)
            return cast(bytes, await resp["Body"].read())
