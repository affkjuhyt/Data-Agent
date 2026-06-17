"""
MinIO connector — follows the DatabaseConnector protocol.
URL format: http://access_key:secret_key@host:port
            or http://host:port (credentials via env / set_credentials)
"""
import asyncio
from typing import BinaryIO
from urllib.parse import urlparse

import aioboto3
from botocore.exceptions import ClientError

from basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig
from services.log_set_up import create_logger

log = create_logger("services.minio", "service_minio")


class MinIOClient:
    __slots__ = (
        "_session", "_endpoint", "_access_key", "_secret_key",
        "_connected", "_healthy", "_url",
    )

    def __init__(self) -> None:
        self._session: aioboto3.Session | None = None
        self._endpoint: str | None = None
        self._access_key: str | None = None
        self._secret_key: str | None = None
        self._connected: bool = False
        self._healthy: bool = False
        self._url: str | None = None

    def set_url(self, url: str) -> None:
        """Accept http://access:secret@host:port or http://host:port."""
        self._url = url
        parsed = urlparse(url)
        if parsed.username and parsed.password:
            self._access_key = parsed.username
            self._secret_key = parsed.password
            port = f":{parsed.port}" if parsed.port else ""
            self._endpoint = f"{parsed.scheme}://{parsed.hostname}{port}"
        else:
            self._endpoint = url

    def set_credentials(self, access_key: str, secret_key: str) -> None:
        self._access_key = access_key
        self._secret_key = secret_key

    def _make_session(self) -> aioboto3.Session:
        return aioboto3.Session()

    def _client_kwargs(self) -> dict:
        return {
            "endpoint_url": self._endpoint,
            "aws_access_key_id": self._access_key,
            "aws_secret_access_key": self._secret_key,
        }

    async def _check_health(self, config: HealthCheckLoopConfig) -> None:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await asyncio.wait_for(s3.list_buckets(), timeout=config.timeout_for_health_check)

    async def _reconnect(self) -> None:
        await self.close()
        try:
            self._session = self._make_session()
            self._connected = True
            self._healthy = True
        except Exception as e:
            log.warning(f"MinIO reconnect failed: {e}")

    async def open(self, retry: RetryConfig = RetryConfig()) -> "MinIOClient":
        if self._endpoint is None:
            raise RuntimeError("MinIO URL is not set — call set_url() first")
        for attempt in range(retry.count):
            try:
                self._session = self._make_session()
                async with self._session.client("s3", **self._client_kwargs()) as s3:
                    await s3.list_buckets()
                self._connected = True
                self._healthy = True
                log.info("MinIO connection established endpoint=%s", self._endpoint)
                return self
            except Exception as e:
                log.info("Attempt #%d/%d: MinIO connection failed — %s", attempt + 1, retry.count, e)
                await asyncio.sleep(3)
        raise ConnectionError(f"Could not connect to MinIO after {retry.count} attempts")

    async def close(self) -> None:
        self._session = None
        self._connected = False
        self._healthy = False
        log.info("MinIO client closed")

    async def health_check_loop(self, config: HealthCheckLoopConfig = HealthCheckLoopConfig()) -> None:
        log.info("MinIO health check loop started")
        while True:
            try:
                await self._check_health(config)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning("MinIO unhealthy: %s — attempting reconnect", e)
                await self._reconnect()
            await asyncio.sleep(config.interval)

    def is_healthy(self) -> bool:
        return self._healthy

    def is_connected(self) -> bool:
        return self._connected

    def get_client(self) -> "MinIOClient":
        if not self._connected or self._session is None:
            raise RuntimeError("MinIOClient is not open — call open() first")
        return self

    # ── Operations ────────────────────────────────────────────────────────────

    async def ensure_bucket(self, bucket: str) -> None:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            try:
                await s3.head_bucket(Bucket=bucket)
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
                    await s3.create_bucket(Bucket=bucket)
                    log.info("MinIO bucket created bucket=%s", bucket)
                else:
                    raise

    async def upload(self, bucket: str, key: str, data: bytes,
                     content_type: str = "application/octet-stream") -> None:
        import io
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.upload_fileobj(
                io.BytesIO(data), bucket, key,
                ExtraArgs={"ContentType": content_type},
            )
        log.info("MinIO upload bucket=%s key=%s size=%d", bucket, key, len(data))

    async def upload_fileobj(self, bucket: str, key: str, fileobj: BinaryIO,
                             content_type: str = "application/octet-stream") -> None:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.upload_fileobj(fileobj, bucket, key, ExtraArgs={"ContentType": content_type})

    async def download(self, bucket: str, key: str) -> bytes:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            resp = await s3.get_object(Bucket=bucket, Key=key)
            return await resp["Body"].read()

    async def delete(self, bucket: str, key: str) -> None:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.delete_object(Bucket=bucket, Key=key)
        log.info("MinIO delete bucket=%s key=%s", bucket, key)

    async def head(self, bucket: str, key: str) -> dict:
        async with self._session.client("s3", **self._client_kwargs()) as s3:
            resp = await s3.head_object(Bucket=bucket, Key=key)
        return {"content_length": resp["ContentLength"],
                "last_modified": resp["LastModified"].isoformat(),
                "content_type": resp.get("ContentType", "")}


client = MinIOClient()
