"""Dead-letter payloads for failed ingest rows (MinIO) and replay helpers."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from urllib.parse import urlparse

BUCKET = os.environ.get("INGEST_DLQ_BUCKET", "da-ingest")


def _store_config() -> tuple[str, str | None, str | None]:
    raw = os.environ.get("MINIO_URL", "").strip()
    if not raw:
        raise RuntimeError("MINIO_URL is not configured")
    parsed = urlparse(raw)
    endpoint = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 9000}"
    return endpoint, parsed.username, parsed.password


async def store_payload(org_id: uuid.UUID, job_id: uuid.UUID, row: dict[str, Any]) -> str:
    """Write a row JSON object; return the object key used as dlq_record.payload_ref."""
    import aioboto3
    from botocore.config import Config

    key = f"dlq/{org_id}/{job_id}/{uuid.uuid4()}.json"
    body = json.dumps({"columns": list(row.keys()), "values": list(row.values())}).encode()
    endpoint, access_key, secret_key = _store_config()
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    ) as client:
        await client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="application/json")
    return key


async def load_payload(payload_ref: str) -> tuple[list[dict[str, str]], list[Any]]:
    """Return Columns-shaped list and one row of values for materialize."""
    import aioboto3
    from botocore.config import Config

    endpoint, access_key, secret_key = _store_config()
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    ) as client:
        response = await client.get_object(Bucket=BUCKET, Key=payload_ref)
        async with response["Body"] as body:
            data = json.loads(await body.read())
    names = list(data["columns"])
    values = list(data["values"])
    columns = [{"name": n, "label": n, "type": "text"} for n in names]
    return columns, values
